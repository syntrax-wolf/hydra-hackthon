"""Applicant Orchestrator — Two-agent hiring pipeline with required skills gate.

Flow:
  Phase 0-1: Front-door router — LLM classifies intent (hiring/company_info/general)
  Phase 2:   Form building — Interrogator asks questions, Form-Filler extracts answers
  Phase 3:   Auto-triggered — job matching with required skills gate + YouTube for preferred gaps
  Phase 4+:  Job browsing, applications, interview prep (LLM intent classification)

Two-Agent Pattern:
  - Form-Filler LLM: extracts structured data from user messages (no hallucination)
  - Interrogator LLM: generates natural conversational questions for missing fields
  - Communication: mediated through form_state stored in DB

Required Skills Gate:
  - User MUST have ALL required_skills to apply for a job
  - Preferred skills the user lacks → YouTube suggestions
"""

import logging
import json
import re

from applicant.profile_manager import (
    get_profile, get_full_profile, get_skills, get_skill_names,
    get_education, get_experience,
    calculate_completion, save_extracted_data, update_profile_embedding,
)
from agents import applicant_agent

log = logging.getLogger("applicant_orchestrator")


# ── Form State ──────────────────────────────────────────────

def _build_form_state(profile: dict) -> dict:
    """Build the current form state from DB profile data."""
    skills = get_skills(profile["applicant_id"])
    education = get_education(profile["applicant_id"])
    experience = get_experience(profile["applicant_id"])

    form = {
        "full_name": profile.get("full_name"),
        "phone": profile.get("phone"),
        "desired_role": profile.get("desired_role"),
        "desired_department": profile.get("desired_department"),
        "skills": [s["skill_name"] for s in skills] if skills else [],
        "experience_years": profile.get("experience_years"),
        "education": None,
        "current_experience": None,
        "location_preference": profile.get("location_preference") or [],
        "willing_to_relocate": profile.get("willing_to_relocate"),
        "salary_expectation": None,
        "job_type_preference": profile.get("job_type_preference") or None,
        "linkedin_url": profile.get("linkedin_url"),
        "github_url": profile.get("github_url"),
    }

    if education:
        e = education[0]
        form["education"] = {
            "institution": e.get("institution"),
            "degree": e.get("degree"),
            "field_of_study": e.get("field_of_study"),
        }

    if experience:
        e = experience[0]
        form["current_experience"] = {
            "company": e.get("company_name"),
            "role": e.get("role_title"),
            "description": e.get("description"),
        }

    if profile.get("salary_min"):
        form["salary_expectation"] = {
            "min": profile["salary_min"],
            "max": profile.get("salary_max", profile["salary_min"]),
        }

    # Flatten job_type_preference list to single string
    jtp = profile.get("job_type_preference") or []
    if isinstance(jtp, list) and jtp:
        form["job_type_preference"] = jtp[0]

    return form


def _get_missing_fields(form: dict) -> list[str]:
    """Return list of missing form fields."""
    missing = []
    if not form.get("desired_role"):
        missing.append("desired_role")
    if len(form.get("skills") or []) < 1:
        missing.append("skills (need at least 1)")
    if form.get("experience_years") is None:
        missing.append("experience_years")
    if not form.get("education") and not form.get("current_experience"):
        missing.append("education or work experience")
    locs = form.get("location_preference") or []
    if not locs:
        missing.append("location_preference")
    if not form.get("salary_expectation"):
        missing.append("salary_expectation")
    if not form.get("job_type_preference"):
        missing.append("job_type_preference")
    if not form.get("desired_department"):
        missing.append("desired_department")
    if form.get("willing_to_relocate") is None:
        missing.append("willing_to_relocate")
    if not form.get("phone"):
        missing.append("phone")
    if not form.get("linkedin_url"):
        missing.append("linkedin_url")
    if not form.get("github_url"):
        missing.append("github_url")
    return missing


def _is_form_complete(form: dict) -> bool:
    """Check if form has enough data to match jobs.
    Required: desired_role + skills >= 1 + experience_years + (education OR experience)
              + location + salary + job_type
    """
    has_role = bool(form.get("desired_role"))
    has_skills = len(form.get("skills") or []) >= 1
    has_exp_years = form.get("experience_years") is not None
    has_background = bool(form.get("education")) or bool(form.get("current_experience"))
    has_location = bool(form.get("location_preference"))
    has_salary = bool(form.get("salary_expectation"))
    has_job_type = bool(form.get("job_type_preference"))
    return (has_role and has_skills and has_exp_years and has_background
            and has_location and has_salary and has_job_type)


# ── Form-Filler Save ────────────────────────────────────────

def _save_form_delta(applicant_id: int, delta: dict) -> int:
    """Convert Form-Filler LLM output to save_extracted_data format and persist.
    Returns new completion %.
    """
    extracted = {}

    # Profile fields
    profile = {}
    if delta.get("full_name"):
        profile["full_name"] = delta["full_name"]
    if delta.get("desired_role"):
        profile["desired_role"] = delta["desired_role"]
    if delta.get("experience_years") is not None:
        profile["experience_years"] = delta["experience_years"]
    if delta.get("location_preference"):
        profile["location_preference"] = delta["location_preference"]
    if delta.get("job_type_preference"):
        profile["job_type_preference"] = delta["job_type_preference"]
    if delta.get("phone"):
        profile["phone"] = delta["phone"]
    if delta.get("desired_department"):
        profile["desired_department"] = delta["desired_department"]
    if delta.get("willing_to_relocate") is not None:
        val = delta["willing_to_relocate"]
        if isinstance(val, str):
            val = val.lower().strip() in ("yes", "true", "1")
        profile["willing_to_relocate"] = bool(val)
    if delta.get("linkedin_url"):
        profile["linkedin_url"] = delta["linkedin_url"]
    if delta.get("github_url"):
        profile["github_url"] = delta["github_url"]
    if delta.get("salary_expectation"):
        extracted["salary_expectation"] = delta["salary_expectation"]

    if profile:
        extracted["profile"] = profile

    # Skills — convert from list of strings to list of dicts
    if delta.get("skills"):
        extracted["skills"] = [
            {"skill_name": s, "proficiency_level": "intermediate"}
            if isinstance(s, str) else s
            for s in delta["skills"]
        ]

    # Education
    if delta.get("education") and isinstance(delta["education"], dict):
        extracted["education"] = delta["education"]

    # Experience
    if delta.get("current_experience") and isinstance(delta["current_experience"], dict):
        exp = delta["current_experience"]
        extracted["experience"] = [{
            "company_name": exp.get("company", ""),
            "role_title": exp.get("role", ""),
            "is_current": True,
            "description": exp.get("description"),
        }]

    if extracted:
        completion = save_extracted_data(applicant_id, extracted)
    else:
        completion = calculate_completion(applicant_id)

    return completion


# ── Eligibility Gate ────────────────────────────────────────

def _compute_eligibility(job: dict, user_skills: set) -> dict:
    """Compute whether user can apply + what they're missing."""
    required = job.get("required_skills", [])
    if isinstance(required, str):
        required = json.loads(required)
    preferred = job.get("preferred_skills", [])
    if isinstance(preferred, str):
        preferred = json.loads(preferred)

    required_set = set(s.lower() for s in required)
    preferred_set = set(s.lower() for s in preferred)

    matched_required = required_set & user_skills
    missing_required = required_set - user_skills
    matched_preferred = preferred_set & user_skills
    missing_preferred = preferred_set - user_skills

    can_apply = len(missing_required) == 0

    return {
        "can_apply": can_apply,
        "matched_required": sorted(matched_required),
        "missing_required": sorted(missing_required),
        "matched_preferred": sorted(matched_preferred),
        "missing_preferred": sorted(missing_preferred),
    }


# ── Skill Confirmation Step ──────────────────────────────────

async def _transition_to_skill_confirmation(applicant_id: int, profile: dict,
                                             form_state: dict, completion: int) -> dict:
    """When form is complete, check if there are commonly required skills the user
    may have forgotten to mention. Ask about them before showing job results.

    This prevents users from being wrongly blocked because they forgot to list
    obvious skills (like Python) that they actually know.
    """
    from applicant.job_matcher import search_jobs
    from core.db import execute_write
    import json

    user_skills = set(s.lower() for s in (form_state.get("skills") or []))

    # Pre-search matching jobs to find commonly required skills
    matched_jobs = search_jobs(applicant_id, limit=15)

    # Collect all required skills across matched jobs
    all_required = set()
    for job in matched_jobs:
        req = job.get("required_skills", [])
        if isinstance(req, str):
            req = json.loads(req)
        all_required.update(s.lower() for s in req)

    # Skills that jobs require but the user hasn't mentioned
    potentially_forgotten = all_required - user_skills

    if potentially_forgotten:
        # Transition to Phase 3 (skill confirmation)
        execute_write(
            "UPDATE applicant.applicant_profiles SET onboarding_phase = 3 WHERE applicant_id = %s",
            [applicant_id],
        )

        # Ask the user about these skills
        try:
            confirmation = await applicant_agent.confirm_skills(
                list(user_skills), sorted(potentially_forgotten)
            )
            question_text = confirmation.get("message",
                f"Before I show you jobs, many positions require: {', '.join(sorted(potentially_forgotten))}. Do you have experience with any of these?")
        except Exception as e:
            log.warning("[ORCHESTRATOR] Skill confirmation LLM failed: %s", e)
            question_text = f"Almost there! Many jobs I found require: {', '.join(sorted(potentially_forgotten)[:8])}. Do you know any of these? Just tell me which ones you have experience with."

        return {
            "type": "applicant",
            "step": "skill_confirmation",
            "message": question_text,
            "form_state": form_state,
            "potentially_forgotten_skills": sorted(potentially_forgotten),
            "progress": {
                "percentage": completion,
                "filled_fields": [k for k, v in form_state.items() if v],
                "missing_fields": [],
            },
        }

    # No common missing skills — go directly to job results
    execute_write(
        "UPDATE applicant.applicant_profiles SET onboarding_phase = 4 WHERE applicant_id = %s",
        [applicant_id],
    )
    results = await _get_job_results_with_gate(applicant_id)
    return {
        "type": "applicant",
        "step": "job_results",
        "message": "Your profile is complete! Here are jobs matching your profile:",
        "profile_summary": _build_profile_summary(profile),
        "eligible_jobs": results["eligible_jobs"],
        "blocked_jobs": results["blocked_jobs"],
        "progress": {"percentage": completion},
    }


# ── Job Results with Gate ───────────────────────────────────

async def _get_job_results_with_gate(applicant_id: int) -> dict:
    """Search jobs, partition into eligible/blocked, add YouTube for preferred gaps."""
    from applicant.job_matcher import search_jobs
    from applicant.youtube_search import get_recommendations_for_gaps
    from core.db import execute_write

    profile = get_full_profile(applicant_id)
    user_skills = set(s["skill_name"].lower() for s in profile.get("skills", []))

    # Search matching jobs
    matched_jobs = search_jobs(applicant_id, limit=20)

    eligible_jobs = []
    blocked_jobs = []

    for job in matched_jobs:
        eligibility = _compute_eligibility(job, user_skills)
        job_entry = {
            **job,
            "can_apply": eligibility["can_apply"],
            "matched_required": eligibility["matched_required"],
            "missing_required": eligibility["missing_required"],
            "matched_preferred": eligibility["matched_preferred"],
            "missing_preferred": eligibility["missing_preferred"],
        }

        if eligibility["can_apply"]:
            eligible_jobs.append(job_entry)
        else:
            job_entry["block_reason"] = f"Missing required skills: {', '.join(eligibility['missing_required'])}"
            blocked_jobs.append(job_entry)

    # YouTube suggestions for preferred skill gaps across eligible jobs
    all_preferred_gaps = set()
    for job in eligible_jobs:
        all_preferred_gaps.update(job["missing_preferred"])

    youtube_recs = []
    if all_preferred_gaps:
        gap_list = [{"skill": s, "priority": i + 1} for i, s in enumerate(sorted(all_preferred_gaps)[:5])]
        try:
            youtube_recs = await get_recommendations_for_gaps(gap_list)
        except Exception as e:
            log.warning("[ORCHESTRATOR] YouTube failed: %s", e)

    # Attach YouTube to eligible jobs' preferred gaps
    yt_map = {r["skill"].lower(): r for r in youtube_recs}
    for job in eligible_jobs:
        job["youtube_suggestions"] = [
            yt_map[s] for s in job["missing_preferred"] if s in yt_map
        ]

    # Update phase
    execute_write(
        "UPDATE applicant.applicant_profiles SET onboarding_phase = 4 WHERE applicant_id = %s",
        [applicant_id],
    )

    return {
        "eligible_jobs": eligible_jobs,
        "blocked_jobs": blocked_jobs,
    }


# ── Profile Summary Builder ─────────────────────────────────

def _build_profile_summary(profile: dict) -> dict:
    """Build a read-only summary of all saved profile data."""
    skills = get_skills(profile["applicant_id"])
    education = get_education(profile["applicant_id"])
    experience = get_experience(profile["applicant_id"])

    summary = {}
    if profile.get("full_name"):
        summary["name"] = profile["full_name"]
    if profile.get("desired_role"):
        summary["desired_role"] = profile["desired_role"]
    if profile.get("experience_years") is not None:
        summary["experience_years"] = profile["experience_years"]
    if skills:
        summary["skills"] = [s["skill_name"] for s in skills]
    if education:
        summary["education"] = [f"{e['institution']} — {e.get('degree', '')} {e.get('field_of_study', '')}" for e in education]
    if experience:
        summary["experience"] = [f"{e['role_title']} at {e['company_name']}" + (" (current)" if e.get("is_current") else "") for e in experience]
    locs = profile.get("location_preference") or []
    if locs:
        summary["location"] = locs if isinstance(locs, list) else [locs]
    if profile.get("salary_min"):
        summary["salary"] = f"{profile['salary_min']:.0f} - {profile.get('salary_max', profile['salary_min']):.0f}"

    return summary


# ── Post-profile intent handlers ────────────────────────────

async def handle_application(applicant_id: int, message: str) -> dict:
    """Handle job application with required skills gate."""
    from applicant.application_manager import get_job_by_id, has_existing_application
    from applicant.job_matcher import enrich_job_match, search_jobs
    from applicant.youtube_search import get_recommendations_for_gaps
    from core.db import execute_read

    job = None

    # Try to find job by number first (e.g., "apply to job #5")
    num_match = re.search(r'#(\d+)', message)
    if num_match:
        job = get_job_by_id(int(num_match.group(1)))

    # If no number, search by title — try HydraDB semantic search first, then PostgreSQL LIKE
    if not job:
        search_text = re.sub(r'(?i)(i want to |apply for |apply to |apply |submit application for )', '', message).strip()
        if search_text:
            # Try HydraDB semantic search
            try:
                from applicant.hydra_retriever import search_job_by_title_hydra
                hydra_job = search_job_by_title_hydra(search_text)
                if hydra_job:
                    job = get_job_by_id(hydra_job["job_id"])
                    log.info("[ORCHESTRATOR] HydraDB matched job: %s (ID: %d)", hydra_job["title"], hydra_job["job_id"])
            except Exception as e:
                log.warning("[ORCHESTRATOR] HydraDB title search failed: %s", e)

            # Fallback: PostgreSQL LIKE search
            if not job:
                rows = execute_read(
                    """SELECT job_id, title, company FROM applicant.job_postings
                       WHERE status = 'open'
                       AND (LOWER(title) LIKE %s OR LOWER(title) LIKE %s)
                       ORDER BY
                           CASE WHEN LOWER(title) = %s THEN 0
                                WHEN LOWER(title) LIKE %s THEN 1
                                ELSE 2 END
                       LIMIT 1""",
                    [f"%{search_text.lower()}%", f"%{search_text.lower().replace(' ', '%')}%",
                     search_text.lower(), f"%{search_text.lower()}%"],
                )
                if rows:
                    job = get_job_by_id(rows[0][0])

    if not job:
        search_text = re.sub(r'(?i)(i want to |apply for |apply to |apply |submit application for )', '', message).strip()
        jobs = search_jobs(applicant_id, query=search_text or None, limit=10)
        return {"type": "applicant", "step": "job_discovery",
                "message": f"I couldn't find an exact match for \"{search_text}\". Here are the closest jobs:",
                "jobs": jobs}

    # ── REQUIRED SKILLS GATE ──
    profile = get_full_profile(applicant_id)
    user_skills = set(s["skill_name"].lower() for s in profile.get("skills", []))
    eligibility = _compute_eligibility(job, user_skills)

    if not eligibility["can_apply"]:
        # BLOCKED — missing required skills
        missing = eligibility["missing_required"]
        gap_list = [{"skill": s, "priority": i + 1} for i, s in enumerate(missing[:5])]
        try:
            youtube_recs = await get_recommendations_for_gaps(gap_list)
        except Exception:
            youtube_recs = []
        return {
            "type": "applicant", "step": "application_blocked",
            "message": f"You cannot apply to \"{job['title']}\" at {job['company']} because you're missing required skills: {', '.join(missing)}.",
            "job": {"job_id": job["job_id"], "title": job["title"], "company": job["company"]},
            "missing_required": missing,
            "youtube_suggestions": youtube_recs,
        }

    # Eligible — proceed with application
    job_id = job["job_id"]
    if has_existing_application(applicant_id, job_id):
        return {"type": "applicant", "step": "error", "message": "You've already applied to this job."}

    cover_letter = await applicant_agent.generate_cover_letter(profile, job)
    enriched = enrich_job_match(job, profile)

    return {
        "type": "applicant", "step": "application_preview",
        "message": "Review your application before submitting:",
        "job": {"job_id": job["job_id"], "title": job["title"], "company": job["company"]},
        "match_score": enriched.get("match_score", 0),
        "matched_skills": enriched.get("matched_skills", []),
        "missing_skills": enriched.get("missing_skills", []),
        "cover_letter": cover_letter,
        "actions": ["submit", "revise", "cancel"],
    }


async def handle_interview_prep(applicant_id: int) -> dict:
    from applicant.application_manager import get_latest_application, get_job_by_id
    from applicant.youtube_search import get_recommendations_for_gaps
    from core.db import execute_read, execute_write

    app = get_latest_application(applicant_id)
    if not app:
        return {"type": "applicant", "step": "error", "message": "Apply to a job first."}

    job = get_job_by_id(app["job_id"])
    profile = get_full_profile(applicant_id)

    cached = execute_read("SELECT content FROM applicant.interview_prep WHERE application_id = %s", [app["application_id"]])
    if cached:
        content = cached[0][0] if isinstance(cached[0][0], dict) else json.loads(cached[0][0])
    else:
        content = await applicant_agent.generate_interview_prep(profile, job)
        execute_write("INSERT INTO applicant.interview_prep (application_id, content) VALUES (%s, %s)",
                      [app["application_id"], json.dumps(content)])

    # Compute skill gap + YouTube recommendations
    user_skills = set(s["skill_name"].lower() for s in profile.get("skills", []))
    eligibility = _compute_eligibility(job, user_skills)
    missing_preferred = eligibility["missing_preferred"]
    missing_required = eligibility["missing_required"]
    all_gaps = missing_required + missing_preferred

    skill_gap = None
    if all_gaps:
        gap_list = [{"skill": s, "priority": i + 1} for i, s in enumerate(all_gaps[:5])]
        try:
            youtube_recs = await get_recommendations_for_gaps(gap_list)
        except Exception:
            youtube_recs = []

        # Build skill gap analysis object for the frontend
        critical_gaps = []
        for gap in gap_list:
            yt = next((r for r in youtube_recs if r["skill"].lower() == gap["skill"].lower()), None)
            critical_gaps.append({
                "skill": gap["skill"],
                "why": "Required skill" if gap["skill"] in missing_required else "Preferred skill",
                "youtube": yt if yt else {"playlists": [], "videos": []},
            })

        total_required = len(eligibility["matched_required"]) + len(missing_required)
        total_preferred = len(eligibility["matched_preferred"]) + len(missing_preferred)
        total = total_required + total_preferred
        matched = len(eligibility["matched_required"]) + len(eligibility["matched_preferred"])
        readiness = int((matched / total * 100)) if total > 0 else 100

        skill_gap = {
            "overall_readiness": readiness,
            "strengths": eligibility["matched_required"] + eligibility["matched_preferred"],
            "critical_gaps": critical_gaps,
        }

    result = {"type": "applicant", "step": "interview_prep",
              "message": f"Interview Prep — {job['title']} at {job['company']}", "prep": content}
    if skill_gap:
        result["skill_gap"] = skill_gap
    return result


async def handle_tracking(applicant_id: int) -> dict:
    from applicant.application_manager import get_all_applications
    applications = get_all_applications(applicant_id)
    grouped = {}
    for app in applications:
        grouped.setdefault(app.get("status", "submitted"), []).append(app)
    return {"type": "applicant", "step": "tracking_dashboard",
            "message": f"You have {len(applications)} application(s):",
            "applications": applications, "grouped": grouped}


async def handle_withdraw(applicant_id: int, message: str) -> dict:
    """Handle application withdrawal request."""
    import re
    from applicant.application_manager import get_all_applications, withdraw_application

    # Try to find app ID from message (e.g., "withdraw #3" or "withdraw application 3")
    num_match = re.search(r'#?(\d+)', message)
    if num_match:
        app_id = int(num_match.group(1))
        success = withdraw_application(app_id, applicant_id)
        if success:
            return {"type": "applicant", "step": "chat_response",
                    "message": f"Application #{app_id} has been withdrawn.",
                    "follow_ups": ["My applications", "Find more jobs"]}
        else:
            return {"type": "applicant", "step": "error",
                    "message": f"Could not withdraw application #{app_id}. It may already be withdrawn, rejected, or accepted."}

    # No ID specified — show applications with withdraw buttons
    applications = get_all_applications(applicant_id)
    active = [a for a in applications if a.get("status") not in ("withdrawn", "rejected", "accepted")]
    if not active:
        return {"type": "applicant", "step": "chat_response",
                "message": "You have no active applications to withdraw.",
                "follow_ups": ["Find jobs", "My applications"]}

    return {"type": "applicant", "step": "withdraw_picker",
            "message": "Which application would you like to withdraw?",
            "applications": active}


# ── Main Entry Point ─────────────────────────────────────────

async def handle_applicant_message(applicant_id: int, message: str,
                                    conversation_history: list = None) -> dict:
    """Main entry point. Two-agent hiring pipeline."""
    profile = get_profile(applicant_id)
    if not profile:
        return {"type": "applicant", "step": "error", "message": "Profile not found."}

    phase = profile.get("onboarding_phase", 1)
    history = conversation_history or []

    # ── Guard: if form isn't complete, force form-building regardless of stored phase ──
    # Phase 3 = skill confirmation (form IS complete but checking forgotten skills)
    # Phase 4+ = post-results. If form somehow incomplete at phase 4+, reset to phase 2.
    if phase >= 4:
        form_state = _build_form_state(profile)
        if not _is_form_complete(form_state):
            from core.db import execute_write as _ew
            _ew("UPDATE applicant.applicant_profiles SET onboarding_phase = 2 WHERE applicant_id = %s",
                [applicant_id])
            phase = 2
            log.info("[ORCHESTRATOR] Form incomplete at phase %d — resetting to phase 2 for applicant %d",
                     profile.get("onboarding_phase", 0), applicant_id)

    # ── Phase 0-1: Front-door routing ──
    if phase <= 1:
        intent = await applicant_agent.classify_front_door(message)
        log.info("[ORCHESTRATOR] Front-door intent: %s (phase=%d)", intent, phase)

        if intent == "hiring":
            # Transition to form-building phase
            from core.db import execute_write
            execute_write(
                "UPDATE applicant.applicant_profiles SET onboarding_phase = 2 WHERE applicant_id = %s",
                [applicant_id],
            )

            # Extract any initial info the user shared
            form_state = _build_form_state(profile)
            try:
                delta = await applicant_agent.fill_form(message, form_state, history)
                completion = _save_form_delta(applicant_id, delta)
            except Exception as e:
                log.warning("[ORCHESTRATOR] Initial form fill failed: %s", e)
                completion = calculate_completion(applicant_id)

            # Update embedding
            try:
                await update_profile_embedding(applicant_id)
            except Exception:
                pass

            # Reload profile and check if form is already complete
            profile = get_full_profile(applicant_id)
            form_state = _build_form_state(profile)

            if _is_form_complete(form_state):
                # Rare but possible — user gave everything in first message
                # Still go through skill confirmation before showing jobs
                return await _transition_to_skill_confirmation(applicant_id, profile, form_state, completion)

            # Not complete — interrogate for missing fields
            missing = _get_missing_fields(form_state)
            try:
                interrogation = await applicant_agent.interrogate(form_state, missing, history)
                question_text = interrogation.get("message", "Tell me more about yourself — what role are you looking for, your skills, and experience?")
            except Exception as e:
                log.warning("[ORCHESTRATOR] Interrogation failed: %s", e)
                question_text = "Great! Let's build your profile. What kind of role are you looking for, and what are your key skills?"

            return {
                "type": "applicant",
                "step": "form_building",
                "message": question_text,
                "form_state": form_state,
                "progress": {
                    "percentage": completion,
                    "filled_fields": [k for k, v in form_state.items() if v],
                    "missing_fields": missing,
                },
            }

        else:
            # General or company_info — simple response
            return {
                "type": "applicant",
                "step": "chat_response",
                "message": "Welcome to Horizon Technologies! I'm here to help you find the perfect job. Tell me about yourself — what kind of role are you looking for?",
            }

    # ── Phase 2: Form building (two-agent loop) ──
    if phase == 2:
        form_state = _build_form_state(profile)

        # Step 1: Form-Filler extracts data from message
        try:
            delta = await applicant_agent.fill_form(message, form_state, history)
            completion = _save_form_delta(applicant_id, delta)
        except Exception as e:
            log.warning("[ORCHESTRATOR] Form fill failed: %s", e)
            completion = calculate_completion(applicant_id)

        # Update embedding
        try:
            await update_profile_embedding(applicant_id)
        except Exception:
            pass

        # Reload and check completion
        profile = get_full_profile(applicant_id)
        form_state = _build_form_state(profile)

        if _is_form_complete(form_state):
            # Form structurally complete — but before showing jobs,
            # check if there are common required skills the user may have FORGOTTEN
            return await _transition_to_skill_confirmation(applicant_id, profile, form_state, completion)

        # Step 2: Interrogator asks next questions
        missing = _get_missing_fields(form_state)
        try:
            interrogation = await applicant_agent.interrogate(form_state, missing, history)
            question_text = interrogation.get("message", "Tell me more to complete your profile.")
        except Exception as e:
            log.warning("[ORCHESTRATOR] Interrogation failed: %s", e)
            # Fallback: generate simple questions from missing fields
            if "desired_role" in str(missing):
                question_text = "What kind of role are you looking for?"
            elif "skills" in str(missing):
                question_text = "What are your key technical skills?"
            else:
                question_text = "Can you tell me more about your education, location preference, or salary expectations?"

        return {
            "type": "applicant",
            "step": "form_building",
            "message": question_text,
            "form_state": form_state,
            "progress": {
                "percentage": completion,
                "filled_fields": [k for k, v in form_state.items() if v],
                "missing_fields": missing,
            },
        }

    # ── Phase 3: Skill confirmation — user responding about forgotten skills ──
    if phase == 3:
        from core.db import execute_write

        # Extract any additional skills from the user's response
        form_state = _build_form_state(profile)
        try:
            delta = await applicant_agent.fill_form(message, form_state, history)
            completion = _save_form_delta(applicant_id, delta)
        except Exception as e:
            log.warning("[ORCHESTRATOR] Skill confirmation extraction failed: %s", e)
            completion = calculate_completion(applicant_id)

        # Update embedding with new skills
        try:
            await update_profile_embedding(applicant_id)
        except Exception:
            pass

        # Transition to Phase 4 and show job results
        execute_write(
            "UPDATE applicant.applicant_profiles SET onboarding_phase = 4 WHERE applicant_id = %s",
            [applicant_id],
        )
        profile = get_full_profile(applicant_id)
        results = await _get_job_results_with_gate(applicant_id)
        return {
            "type": "applicant",
            "step": "job_results",
            "message": "Got it! Here are jobs matching your updated profile:",
            "profile_summary": _build_profile_summary(profile),
            "eligible_jobs": results["eligible_jobs"],
            "blocked_jobs": results["blocked_jobs"],
            "progress": {"percentage": completion},
        }

    # ── Phase 4+: LLM classifies intent ──
    intent = await applicant_agent.classify_intent(message, phase, profile.get("profile_completion", 0))
    log.info("[ORCHESTRATOR] LLM intent: %s (phase=%d)", intent, phase)

    if intent == "apply_job":
        return await handle_application(applicant_id, message)
    elif intent == "interview_prep":
        return await handle_interview_prep(applicant_id)
    elif intent == "my_applications":
        return await handle_tracking(applicant_id)
    elif intent == "withdraw_application":
        return await handle_withdraw(applicant_id, message)
    elif intent == "show_profile":
        return {"type": "applicant", "step": "show_profile", "profile": get_full_profile(applicant_id)}
    elif intent == "edit_profile":
        return {"type": "applicant", "step": "edit_profile", "profile": get_full_profile(applicant_id),
                "message": "Here's your profile. You can edit any field below, or just tell me what you'd like to change."}
    elif intent == "find_jobs":
        results = await _get_job_results_with_gate(applicant_id)
        return {
            "type": "applicant",
            "step": "job_results",
            "message": "Here are jobs matching your profile:",
            "eligible_jobs": results["eligible_jobs"],
            "blocked_jobs": results["blocked_jobs"],
        }
    elif intent == "profile_info":
        # User sharing more info — run through form-filler and update
        form_state = _build_form_state(profile)
        try:
            delta = await applicant_agent.fill_form(message, form_state, history)
            completion = _save_form_delta(applicant_id, delta)
        except Exception:
            completion = calculate_completion(applicant_id)
        try:
            await update_profile_embedding(applicant_id)
        except Exception:
            pass
        profile = get_full_profile(applicant_id)
        return {"type": "applicant", "step": "profile_progress",
                "message": "Updated your profile!",
                "profile_summary": _build_profile_summary(profile),
                "progress": {"percentage": completion}}
    else:
        # Default: show jobs
        results = await _get_job_results_with_gate(applicant_id)
        return {
            "type": "applicant",
            "step": "job_results",
            "message": "Here are jobs for you:",
            "eligible_jobs": results["eligible_jobs"],
            "blocked_jobs": results["blocked_jobs"],
        }
