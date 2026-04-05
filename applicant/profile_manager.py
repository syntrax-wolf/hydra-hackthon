"""Profile Manager — CRUD operations for applicant profiles, skills, education, experience.

All DB access uses execute_read/execute_write from core.db.
"""

import json
import logging
from datetime import datetime
from core.db import execute_read, execute_write

log = logging.getLogger("profile_manager")

# Sections needed for profile completion scoring
PROFILE_SECTIONS = {
    "basic": ["desired_role", "experience_years"],
    "skills": 3,  # minimum 3 skills
    "education": 1,  # at least 1 entry
    "experience": 1,  # at least 1 entry
    "preferences": ["salary_min", "location_preference"],
}


# ── Profile CRUD ─────────────────────────────────────────────

def create_profile(email: str, full_name: str = None) -> int:
    """Create a new applicant profile. Returns applicant_id."""
    rows = execute_read(
        "SELECT applicant_id FROM applicant.applicant_profiles WHERE email = %s",
        [email],
    )
    if rows:
        return rows[0][0]

    execute_write(
        "INSERT INTO applicant.applicant_profiles (email, full_name) VALUES (%s, %s)",
        [email, full_name],
    )
    rows = execute_read(
        "SELECT applicant_id FROM applicant.applicant_profiles WHERE email = %s",
        [email],
    )
    log.info("[PROFILE] Created profile for %s (id=%d)", email, rows[0][0])
    return rows[0][0]


def get_profile(applicant_id: int) -> dict | None:
    """Fetch basic profile info."""
    rows = execute_read(
        "SELECT applicant_id, full_name, email, phone, headline, summary, "
        "desired_role, desired_department, experience_years, current_company, current_role, "
        "location_preference, willing_to_relocate, salary_min, salary_max, salary_currency, "
        "job_type_preference, linkedin_url, github_url, portfolio_url, "
        "resume_file_path, resume_updated_at, onboarding_phase, profile_completion, "
        "created_at, updated_at "
        "FROM applicant.applicant_profiles WHERE applicant_id = %s",
        [applicant_id],
    )
    if not rows:
        return None
    r = rows[0]
    return {
        "applicant_id": r[0], "full_name": r[1], "email": r[2], "phone": r[3],
        "headline": r[4], "summary": r[5], "desired_role": r[6],
        "desired_department": r[7], "experience_years": r[8],
        "current_company": r[9], "current_role": r[10],
        "location_preference": r[11] if isinstance(r[11], list) else json.loads(r[11] or "[]"),
        "willing_to_relocate": r[12], "salary_min": float(r[13]) if r[13] else None,
        "salary_max": float(r[14]) if r[14] else None, "salary_currency": r[15],
        "job_type_preference": r[16] if isinstance(r[16], list) else json.loads(r[16] or "[]"),
        "linkedin_url": r[17], "github_url": r[18], "portfolio_url": r[19],
        "resume_file_path": r[20],
        "resume_updated_at": r[21].isoformat() if r[21] else None,
        "onboarding_phase": r[22], "profile_completion": r[23],
        "created_at": r[24].isoformat() if r[24] else None,
        "updated_at": r[25].isoformat() if r[25] else None,
    }


def get_full_profile(applicant_id: int) -> dict | None:
    """Full profile including skills, education, experience lists."""
    profile = get_profile(applicant_id)
    if not profile:
        return None
    profile["skills"] = get_skills(applicant_id)
    profile["education"] = get_education(applicant_id)
    profile["experience"] = get_experience(applicant_id)
    return profile


def update_profile(applicant_id: int, fields: dict) -> None:
    """Update specific fields on the applicant profile."""
    allowed = {
        "full_name", "phone", "headline", "summary", "desired_role",
        "desired_department", "experience_years", "current_company", "current_role",
        "location_preference", "willing_to_relocate", "salary_min", "salary_max",
        "salary_currency", "job_type_preference", "linkedin_url", "github_url",
        "portfolio_url",
    }
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return

    # Serialize JSONB fields
    for key in ("location_preference", "job_type_preference"):
        if key in updates and isinstance(updates[key], list):
            updates[key] = json.dumps(updates[key])

    set_clause = ", ".join(f"{k} = %s" for k in updates)
    values = list(updates.values()) + [datetime.now(), applicant_id]
    execute_write(
        f"UPDATE applicant.applicant_profiles SET {set_clause}, updated_at = %s WHERE applicant_id = %s",
        values,
    )

    # Recalculate completion
    completion = calculate_completion(applicant_id)
    execute_write(
        "UPDATE applicant.applicant_profiles SET profile_completion = %s WHERE applicant_id = %s",
        [completion, applicant_id],
    )
    log.info("[PROFILE] Updated %d fields for applicant %d (completion=%d%%)",
             len(updates), applicant_id, completion)


# ── Skills CRUD ──────────────────────────────────────────────

def get_skills(applicant_id: int) -> list[dict]:
    rows = execute_read(
        "SELECT skill_id, skill_name, proficiency_level, years_of_experience "
        "FROM applicant.skills WHERE applicant_id = %s ORDER BY skill_id",
        [applicant_id],
    )
    return [{"skill_id": r[0], "skill_name": r[1], "proficiency_level": r[2],
             "years_of_experience": r[3]} for r in rows]


def add_skill(applicant_id: int, skill_name: str,
              proficiency_level: str = "intermediate", years: int = 0) -> int:
    # Check for duplicate
    existing = execute_read(
        "SELECT skill_id FROM applicant.skills WHERE applicant_id = %s AND LOWER(skill_name) = LOWER(%s)",
        [applicant_id, skill_name],
    )
    if existing:
        return existing[0][0]

    execute_write(
        "INSERT INTO applicant.skills (applicant_id, skill_name, proficiency_level, years_of_experience) "
        "VALUES (%s, %s, %s, %s)",
        [applicant_id, skill_name, proficiency_level, years],
    )
    rows = execute_read(
        "SELECT skill_id FROM applicant.skills WHERE applicant_id = %s AND skill_name = %s",
        [applicant_id, skill_name],
    )
    return rows[0][0] if rows else 0


def update_skill(skill_id: int, fields: dict) -> None:
    allowed = {"skill_name", "proficiency_level", "years_of_experience"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return
    set_clause = ", ".join(f"{k} = %s" for k in updates)
    values = list(updates.values()) + [skill_id]
    execute_write(f"UPDATE applicant.skills SET {set_clause} WHERE skill_id = %s", values)


def delete_skill(skill_id: int) -> None:
    execute_write("DELETE FROM applicant.skills WHERE skill_id = %s", [skill_id])


# ── Education CRUD ───────────────────────────────────────────

def get_education(applicant_id: int) -> list[dict]:
    rows = execute_read(
        "SELECT education_id, institution, degree, field_of_study, start_year, end_year, gpa_grade "
        "FROM applicant.education WHERE applicant_id = %s ORDER BY end_year DESC NULLS FIRST",
        [applicant_id],
    )
    return [{"education_id": r[0], "institution": r[1], "degree": r[2],
             "field_of_study": r[3], "start_year": r[4], "end_year": r[5],
             "gpa_grade": r[6]} for r in rows]


def add_education(applicant_id: int, data: dict) -> int:
    execute_write(
        "INSERT INTO applicant.education (applicant_id, institution, degree, field_of_study, start_year, end_year, gpa_grade) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
        [applicant_id, data.get("institution", ""), data.get("degree"),
         data.get("field_of_study"), data.get("start_year"), data.get("end_year"),
         data.get("gpa_grade")],
    )
    rows = execute_read(
        "SELECT MAX(education_id) FROM applicant.education WHERE applicant_id = %s",
        [applicant_id],
    )
    return rows[0][0] if rows else 0


def update_education(education_id: int, fields: dict) -> None:
    allowed = {"institution", "degree", "field_of_study", "start_year", "end_year", "gpa_grade"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return
    set_clause = ", ".join(f"{k} = %s" for k in updates)
    values = list(updates.values()) + [education_id]
    execute_write(f"UPDATE applicant.education SET {set_clause} WHERE education_id = %s", values)


def delete_education(education_id: int) -> None:
    execute_write("DELETE FROM applicant.education WHERE education_id = %s", [education_id])


# ── Experience CRUD ──────────────────────────────────────────

def get_experience(applicant_id: int) -> list[dict]:
    rows = execute_read(
        "SELECT experience_id, company_name, role_title, start_date, end_date, is_current, description "
        "FROM applicant.experience WHERE applicant_id = %s ORDER BY start_date DESC NULLS FIRST",
        [applicant_id],
    )
    return [{"experience_id": r[0], "company_name": r[1], "role_title": r[2],
             "start_date": r[3].isoformat() if r[3] else None,
             "end_date": r[4].isoformat() if r[4] else None,
             "is_current": r[5], "description": r[6]} for r in rows]


def add_experience(applicant_id: int, data: dict) -> int:
    execute_write(
        "INSERT INTO applicant.experience (applicant_id, company_name, role_title, start_date, end_date, is_current, description) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
        [applicant_id, data.get("company_name", ""), data.get("role_title", ""),
         data.get("start_date"), data.get("end_date"),
         data.get("is_current", False), data.get("description")],
    )
    rows = execute_read(
        "SELECT MAX(experience_id) FROM applicant.experience WHERE applicant_id = %s",
        [applicant_id],
    )
    return rows[0][0] if rows else 0


def update_experience(experience_id: int, fields: dict) -> None:
    allowed = {"company_name", "role_title", "start_date", "end_date", "is_current", "description"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return
    set_clause = ", ".join(f"{k} = %s" for k in updates)
    values = list(updates.values()) + [experience_id]
    execute_write(f"UPDATE applicant.experience SET {set_clause} WHERE experience_id = %s", values)


def delete_experience(experience_id: int) -> None:
    execute_write("DELETE FROM applicant.experience WHERE experience_id = %s", [experience_id])


# ── Completion + Embedding ───────────────────────────────────

def calculate_completion(applicant_id: int) -> int:
    """Calculate profile completion percentage (0-100)."""
    profile = get_profile(applicant_id)
    if not profile:
        return 0

    total_weight = 0
    earned = 0

    # Basic info (30 points)
    total_weight += 30
    if profile.get("desired_role"):
        earned += 15
    if profile.get("experience_years") is not None:
        earned += 15

    # Skills (25 points)
    total_weight += 25
    skills = get_skills(applicant_id)
    if len(skills) >= 3:
        earned += 25
    elif len(skills) >= 1:
        earned += 10

    # Education (15 points)
    total_weight += 15
    edu = get_education(applicant_id)
    if len(edu) >= 1:
        earned += 15

    # Experience (15 points)
    total_weight += 15
    exp = get_experience(applicant_id)
    if len(exp) >= 1:
        earned += 15

    # Preferences (15 points)
    total_weight += 15
    if profile.get("salary_min"):
        earned += 8
    loc = profile.get("location_preference") or []
    if len(loc) >= 1:
        earned += 7

    return min(100, int((earned / total_weight) * 100)) if total_weight > 0 else 0


def get_skill_names(applicant_id: int) -> list[str]:
    """Return just the skill names for an applicant."""
    rows = execute_read(
        "SELECT skill_name FROM applicant.skills WHERE applicant_id = %s",
        [applicant_id],
    )
    return [r[0] for r in rows]


def save_confirmation_data(applicant_id: int, data: dict) -> None:
    """Batch save from a confirmation card (legacy)."""
    save_extracted_data(applicant_id, data)


def save_extracted_data(applicant_id: int, extracted: dict) -> int:
    """Auto-save LLM extraction output to DB. Handles both flat and nested formats.
    Returns the new completion percentage.
    """
    # ── Flatten nested "profile" dict ──
    profile_fields = {}
    nested = extracted.get("profile", {})
    if isinstance(nested, dict):
        profile_fields.update(nested)

    # Normalize common LLM field name variations → canonical names
    _aliases = {
        "location": "location_preference", "preferred_location": "location_preference",
        "locations": "location_preference", "preferred_locations": "location_preference",
        "expected_salary": "salary_expectation", "salary": "salary_expectation",
        "salary_range": "salary_expectation", "salary_expectations": "salary_expectation",
        "employment_type": "job_type_preference", "job_type": "job_type_preference",
        "role": "desired_role", "desired_position": "desired_role",
        "years_of_experience": "experience_years", "exp_years": "experience_years",
        "company": "current_company", "current_employer": "current_company",
    }
    for old_key, new_key in _aliases.items():
        if old_key in profile_fields and new_key not in profile_fields:
            profile_fields[new_key] = profile_fields.pop(old_key)
        elif old_key in extracted and new_key not in profile_fields:
            profile_fields[new_key] = extracted[old_key]

    # Also pick up flat top-level profile fields
    for field in ["desired_role", "desired_department", "experience_years",
                  "current_company", "current_role", "full_name", "phone",
                  "willing_to_relocate", "location_preference", "job_type_preference"]:
        val = extracted.get(field)
        if val is not None and field not in profile_fields:
            profile_fields[field] = val

    # Normalize location_preference — LLM might return string instead of list
    loc = profile_fields.get("location_preference")
    if loc and isinstance(loc, str):
        # "Bangalore or Remote" → ["Bangalore", "Remote"]
        parts = [p.strip() for p in loc.replace(" or ", ",").replace(" and ", ",").replace("/", ",").split(",") if p.strip()]
        profile_fields["location_preference"] = parts

    # Normalize job_type_preference — same issue
    jt = profile_fields.get("job_type_preference") or profile_fields.get("employment_type")
    if jt and isinstance(jt, str):
        parts = [p.strip().lower().replace(" ", "_").replace("-", "_") for p in jt.replace(",", " ").split() if p.strip()]
        profile_fields["job_type_preference"] = parts
    if "employment_type" in profile_fields:
        if "job_type_preference" not in profile_fields:
            jt = profile_fields["employment_type"]
            if isinstance(jt, str):
                profile_fields["job_type_preference"] = [jt.strip().lower().replace(" ", "_")]
        del profile_fields["employment_type"]

    # Handle salary_expectation → salary_min/max
    # LLM might return a dict {"min": 1800000, "max": 2500000} or a string "18-25 LPA"
    salary = extracted.get("salary_expectation") or profile_fields.get("salary_expectation")
    if salary:
        if isinstance(salary, dict):
            if salary.get("min"):
                profile_fields["salary_min"] = salary["min"]
            if salary.get("max"):
                profile_fields["salary_max"] = salary["max"]
        elif isinstance(salary, str):
            # Parse "18-25 LPA" or "1800000-2500000"
            import re
            numbers = re.findall(r'[\d.]+', salary)
            if len(numbers) >= 2:
                n1, n2 = float(numbers[0]), float(numbers[1])
                # If numbers are small (like 18, 25), assume LPA → multiply by 100000
                if n1 < 1000:
                    n1 *= 100000
                if n2 < 1000:
                    n2 *= 100000
                profile_fields["salary_min"] = n1
                profile_fields["salary_max"] = n2
            elif len(numbers) == 1:
                n = float(numbers[0])
                if n < 1000:
                    n *= 100000
                profile_fields["salary_min"] = n
        if "salary_expectation" in profile_fields:
            del profile_fields["salary_expectation"]
    for key in ["salary_min", "salary_max"]:
        val = extracted.get(key)
        if val is not None and key not in profile_fields:
            profile_fields[key] = val

    if profile_fields:
        update_profile(applicant_id, profile_fields)
        log.info("[PROFILE] Saved %d profile fields: %s", len(profile_fields), list(profile_fields.keys()))

    # ── Save skills (handle both "skills_mentioned" and "skills") ──
    skills_list = extracted.get("skills_mentioned") or extracted.get("skills") or []
    saved_skills = 0
    for skill in skills_list:
        if isinstance(skill, dict) and skill.get("skill_name"):
            add_skill(
                applicant_id,
                skill["skill_name"],
                skill.get("proficiency_level", "intermediate"),
                skill.get("years_of_experience", 0),
            )
            saved_skills += 1
    if saved_skills:
        log.info("[PROFILE] Saved %d skills", saved_skills)

    # ── Save education (handle dict or list) ──
    edu_data = extracted.get("education")
    saved_edu = 0
    if edu_data and isinstance(edu_data, dict):
        if edu_data.get("institution"):
            add_education(applicant_id, {
                "institution": edu_data.get("institution", ""),
                "degree": edu_data.get("degree"),
                "field_of_study": edu_data.get("field", edu_data.get("field_of_study")),
                "end_year": edu_data.get("year", edu_data.get("end_year")),
                "start_year": edu_data.get("start_year"),
            })
            saved_edu += 1
    elif edu_data and isinstance(edu_data, list):
        for e in edu_data:
            if isinstance(e, dict) and e.get("institution"):
                add_education(applicant_id, {
                    "institution": e.get("institution", ""),
                    "degree": e.get("degree"),
                    "field_of_study": e.get("field_of_study", e.get("field")),
                    "end_year": e.get("end_year", e.get("year")),
                    "start_year": e.get("start_year"),
                })
                saved_edu += 1
    if saved_edu:
        log.info("[PROFILE] Saved %d education entries", saved_edu)

    # ── Save experience (handle list) ──
    exp_data = extracted.get("experience")
    saved_exp = 0
    if exp_data and isinstance(exp_data, list):
        for exp in exp_data:
            if isinstance(exp, dict) and (exp.get("company_name") or exp.get("company") or exp.get("role_title") or exp.get("role")):
                add_experience(applicant_id, {
                    "company_name": exp.get("company_name", exp.get("company", "")),
                    "role_title": exp.get("role_title", exp.get("role", "")),
                    "start_date": exp.get("start_date", exp.get("start")),
                    "end_date": exp.get("end_date", exp.get("end")),
                    "is_current": exp.get("is_current", False),
                    "description": exp.get("description"),
                })
                saved_exp += 1
    # Also handle current_company/current_role as experience
    elif extracted.get("current_company") and extracted.get("current_role"):
        add_experience(applicant_id, {
            "company_name": extracted["current_company"],
            "role_title": extracted["current_role"],
            "is_current": True,
        })
        saved_exp += 1
    if saved_exp:
        log.info("[PROFILE] Saved %d experience entries", saved_exp)

    # ── Recalculate completion ──
    completion = calculate_completion(applicant_id)
    execute_write(
        "UPDATE applicant.applicant_profiles SET profile_completion = %s, updated_at = %s WHERE applicant_id = %s",
        [completion, datetime.now(), applicant_id],
    )
    log.info("[PROFILE] Auto-saved for applicant %d → completion=%d%%", applicant_id, completion)
    return completion


async def update_profile_embedding(applicant_id: int) -> None:
    """Recompute BGE-M3 embedding from current profile state."""
    from applicant.embeddings import compute_embedding

    profile = get_full_profile(applicant_id)
    if not profile:
        return

    skills = profile.get("skills", [])
    experience = profile.get("experience", [])

    text = " ".join(filter(None, [
        profile.get("headline", ""),
        profile.get("summary", ""),
        profile.get("desired_role", ""),
        " ".join(s["skill_name"] for s in skills),
        " ".join(f"{e['role_title']} at {e['company_name']}" for e in experience),
    ]))

    if not text.strip():
        return

    embedding = compute_embedding(text)
    execute_write(
        "UPDATE applicant.applicant_profiles SET profile_embedding = %s, updated_at = %s WHERE applicant_id = %s",
        [str(embedding), datetime.now(), applicant_id],
    )
    log.info("[PROFILE] Updated embedding for applicant %d", applicant_id)
