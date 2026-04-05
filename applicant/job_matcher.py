"""Job Matcher — Hybrid semantic + keyword search against job postings.

Primary: HydraDB recall (semantic + keyword via nasiko_shipathon tenant).
Fallback: PostgreSQL with pgvector embedding + tsvector keyword search.
"""

import json
import logging
from core.db import execute_read
from applicant.profile_manager import get_profile, get_skill_names

log = logging.getLogger("job_matcher")


def search_jobs(applicant_id: int, query: str = None, limit: int = 15) -> list[dict]:
    """Search for matching jobs. Tries HydraDB first, falls back to PostgreSQL."""
    profile = get_profile(applicant_id)
    if not profile:
        return []

    skills = get_skill_names(applicant_id)
    search_text = query or f"{profile.get('desired_role', '')} {' '.join(skills)}"
    search_text = search_text.strip()
    if not search_text:
        search_text = "software developer"

    # ── Try HydraDB recall first ─────────────────────────────────
    try:
        from applicant.hydra_retriever import search_jobs_hydra
        hydra_jobs = search_jobs_hydra(search_text, limit=limit)
        if hydra_jobs:
            log.info("[JOB_MATCHER] HydraDB returned %d jobs for query: %s", len(hydra_jobs), search_text[:50])
            # Enrich with match scores
            enriched = [enrich_job_match(job, profile) for job in hydra_jobs]
            enriched.sort(key=lambda j: j.get("match_score", 0), reverse=True)
            return enriched
        log.info("[JOB_MATCHER] HydraDB returned no jobs, falling back to PostgreSQL")
    except Exception as e:
        log.warning("[JOB_MATCHER] HydraDB recall failed, falling back to PostgreSQL: %s", e)

    # ── Fallback: PostgreSQL ─────────────────────────────────────
    return _search_jobs_postgres(applicant_id, profile, search_text, limit)


def _search_jobs_postgres(applicant_id: int, profile: dict, search_text: str, limit: int) -> list[dict]:
    """Original PostgreSQL hybrid search (fallback)."""
    has_embedding = execute_read(
        "SELECT profile_embedding IS NOT NULL FROM applicant.applicant_profiles WHERE applicant_id = %s",
        [applicant_id],
    )
    use_semantic = has_embedding and has_embedding[0][0]

    if use_semantic:
        rows = execute_read(
            """SELECT j.job_id, j.title, j.company, j.department, j.description,
                      j.required_skills, j.preferred_skills,
                      j.experience_min, j.experience_max,
                      j.salary_min, j.salary_max, j.salary_currency,
                      j.location, j.job_type, j.deadline, j.posted_at,
                      (0.6 * (1 - (j.posting_embedding <=> p.profile_embedding))
                       + 0.4 * COALESCE(ts_rank(j.full_text_search_vector,
                                websearch_to_tsquery('english', %s)), 0)
                      ) AS relevance_score
               FROM applicant.job_postings j
               CROSS JOIN applicant.applicant_profiles p
               WHERE p.applicant_id = %s
                 AND j.status = 'open'
                 AND j.posting_embedding IS NOT NULL
                 AND p.profile_embedding IS NOT NULL
               ORDER BY relevance_score DESC
               LIMIT %s""",
            [search_text, applicant_id, limit],
        )
    else:
        rows = execute_read(
            """SELECT j.job_id, j.title, j.company, j.department, j.description,
                      j.required_skills, j.preferred_skills,
                      j.experience_min, j.experience_max,
                      j.salary_min, j.salary_max, j.salary_currency,
                      j.location, j.job_type, j.deadline, j.posted_at,
                      COALESCE(ts_rank(j.full_text_search_vector,
                               websearch_to_tsquery('english', %s)), 0) AS relevance_score
               FROM applicant.job_postings j
               WHERE j.status = 'open'
               ORDER BY relevance_score DESC
               LIMIT %s""",
            [search_text, limit],
        )

    jobs = []
    for r in rows:
        job = {
            "job_id": r[0], "title": r[1], "company": r[2], "department": r[3],
            "description": r[4],
            "required_skills": r[5] if isinstance(r[5], list) else json.loads(r[5] or "[]"),
            "preferred_skills": r[6] if isinstance(r[6], list) else json.loads(r[6] or "[]"),
            "experience_min": r[7], "experience_max": r[8],
            "salary_min": float(r[9]) if r[9] else None,
            "salary_max": float(r[10]) if r[10] else None,
            "salary_currency": r[11],
            "location": r[12] if isinstance(r[12], list) else json.loads(r[12] or "[]"),
            "job_type": r[13],
            "deadline": r[14].isoformat() if r[14] else None,
            "posted_at": r[15].isoformat() if r[15] else None,
            "relevance_score": round(float(r[16]), 3) if r[16] else 0,
        }
        enriched = enrich_job_match(job, profile)
        jobs.append(enriched)

    jobs.sort(key=lambda j: j.get("match_score", 0), reverse=True)
    return jobs


def enrich_job_match(job: dict, profile: dict) -> dict:
    """Compute detailed match score: skill overlap + experience fit + salary fit."""
    applicant_id = profile.get("applicant_id")
    applicant_skills = set(s.lower() for s in get_skill_names(applicant_id)) if applicant_id else set()

    required = set(s.lower() for s in job.get("required_skills", []))
    preferred = set(s.lower() for s in job.get("preferred_skills", []))

    # Skill match (60 points)
    skill_match = (len(required & applicant_skills) / max(len(required), 1)) * 60 if required else 30

    # Preferred skill match (15 points)
    pref_match = (len(preferred & applicant_skills) / max(len(preferred), 1)) * 15 if preferred else 7

    # Experience fit (15 points)
    exp_years = profile.get("experience_years") or 0
    exp_min = job.get("experience_min") or 0
    exp_max = job.get("experience_max") or 99
    if exp_min <= exp_years <= exp_max:
        exp_match = 15
    elif exp_years < exp_min:
        exp_match = max(0, 15 - (exp_min - exp_years) * 3)
    else:
        exp_match = max(0, 15 - (exp_years - exp_max) * 2)

    # Salary fit (10 points)
    salary_match = 5  # default if no salary data
    if profile.get("salary_min") and job.get("salary_max"):
        if profile["salary_min"] <= job["salary_max"]:
            salary_match = 10
        else:
            salary_match = 2

    total = int(skill_match + pref_match + exp_match + salary_match)

    return {
        **job,
        "match_score": min(100, total),
        "matched_skills": sorted(required & applicant_skills),
        "missing_skills": sorted(required - applicant_skills),
    }


def get_job_by_id(job_id: int) -> dict | None:
    """Fetch a single job posting."""
    rows = execute_read(
        "SELECT job_id, title, company, department, description, "
        "required_skills, preferred_skills, experience_min, experience_max, "
        "salary_min, salary_max, salary_currency, location, job_type, deadline, posted_at "
        "FROM applicant.job_postings WHERE job_id = %s",
        [job_id],
    )
    if not rows:
        return None
    r = rows[0]
    return {
        "job_id": r[0], "title": r[1], "company": r[2], "department": r[3],
        "description": r[4],
        "required_skills": r[5] if isinstance(r[5], list) else json.loads(r[5] or "[]"),
        "preferred_skills": r[6] if isinstance(r[6], list) else json.loads(r[6] or "[]"),
        "experience_min": r[7], "experience_max": r[8],
        "salary_min": float(r[9]) if r[9] else None,
        "salary_max": float(r[10]) if r[10] else None,
        "salary_currency": r[11],
        "location": r[12] if isinstance(r[12], list) else json.loads(r[12] or "[]"),
        "job_type": r[13],
        "deadline": r[14].isoformat() if r[14] else None,
        "posted_at": r[15].isoformat() if r[15] else None,
    }
