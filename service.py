import logging
import uuid
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Cookie
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from core.config import config
from core.schemas import (
    QueryRequest, PipelineResponse, EmailActionRequest, SlotSelectionRequest,
    EmployeeSelectionRequest, ApplicantAuthRequest, ApplicantQueryRequest,
    ProfileConfirmRequest, SkillRequest, EducationRequest, ExperienceRequest,
    ProfileUpdateRequest, ApplicationSubmitRequest,
)
from core.orchestrator import process_query
from core.onboarding_orchestrator import (
    is_onboarding_request, handle_onboarding_message,
    handle_email_action, handle_slot_selection, handle_employee_selection, get_dashboard,
)

# Configure logging for all modules
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)-16s | %(levelname)-5s | %(message)s",
    datefmt="%H:%M:%S",
)

log = logging.getLogger("service")

app = FastAPI(title="Horizon Agent")

# ── Applicant Session Store (in-memory) ──────────────────────
# Maps session_token -> applicant_id
_applicant_sessions: dict[str, int] = {}


def _get_applicant_id(request: Request) -> int:
    """Extract applicant_id from session cookie."""
    token = request.cookies.get("applicant_session")
    if not token or token not in _applicant_sessions:
        raise HTTPException(status_code=401, detail="Not authenticated. Please start a session first.")
    return _applicant_sessions[token]


# ── Landing Page + UI Routes ─────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def serve_landing():
    ui_path = Path(__file__).parent / "ui" / "landing.html"
    return HTMLResponse(content=ui_path.read_text(encoding="utf-8"))


@app.get("/company", response_class=HTMLResponse)
async def serve_company_ui():
    ui_path = Path(__file__).parent / "ui" / "index.html"
    return HTMLResponse(content=ui_path.read_text(encoding="utf-8"))


@app.get("/applicant", response_class=HTMLResponse)
async def serve_applicant_ui():
    ui_path = Path(__file__).parent / "ui" / "applicant.html"
    return HTMLResponse(content=ui_path.read_text(encoding="utf-8"))


# ── Existing Finance + Onboarding Endpoints (unchanged) ──────

@app.post("/api/query")
async def handle_query(req: QueryRequest):
    log.info("Incoming request: query=%r, format=%s, history_turns=%d",
             req.query, req.format, len(req.conversation_history))
    history = [{"role": t.role, "content": t.content} for t in req.conversation_history]

    # Route: onboarding or finance?
    if is_onboarding_request(req.query):
        log.info("Routing to onboarding orchestrator")
        result = await handle_onboarding_message(req.query, history)
        return result

    # Existing finance pipeline (unchanged)
    result = await process_query(req.query, req.format, history)
    log.info("Response: status=%s, time=%dms, file=%s",
             result.get("status"), result.get("time_ms", 0),
             result.get("file", {}).get("name") if result.get("file") else "none")
    return result


@app.post("/api/onboarding/select-employee")
async def onboarding_select_employee(req: EmployeeSelectionRequest):
    log.info("Employee selection: onboarding_id=%d", req.onboarding_id)
    result = await handle_employee_selection(req.onboarding_id)
    return result


@app.post("/api/onboarding/{onboarding_id}/email-action")
async def onboarding_email_action(onboarding_id: int, req: EmailActionRequest):
    log.info("Email action: onboarding_id=%d, action=%s", onboarding_id, req.action)
    result = await handle_email_action(onboarding_id, req.action, req.feedback)
    return result


@app.post("/api/onboarding/{onboarding_id}/select-slot")
async def onboarding_select_slot(onboarding_id: int, req: SlotSelectionRequest):
    log.info("Slot selection: onboarding_id=%d, slot_index=%d", onboarding_id, req.slot_index)
    result = await handle_slot_selection(onboarding_id, req.slot_index)
    return result


@app.get("/api/onboarding/dashboard")
async def onboarding_dashboard():
    log.info("Dashboard request")
    result = await get_dashboard()
    return result


# ── Applicant Auth ───────────────────────────────────────────

@app.post("/api/applicant/auth/start")
async def applicant_auth_start(req: ApplicantAuthRequest):
    """Create or resume an applicant session."""
    from applicant.profile_manager import create_profile, get_profile

    applicant_id = create_profile(req.email, req.full_name)
    token = str(uuid.uuid4())
    _applicant_sessions[token] = applicant_id

    profile = get_profile(applicant_id)
    log.info("Applicant session started: email=%s, id=%d", req.email, applicant_id)

    response = JSONResponse(content={
        "applicant_id": applicant_id,
        "session_token": token,
        "profile": profile,
    })
    response.set_cookie(key="applicant_session", value=token, httponly=True, samesite="lax")
    return response


# ── Applicant Chat ───────────────────────────────────────────

@app.post("/api/applicant/query")
async def applicant_query(req: ApplicantQueryRequest, request: Request):
    applicant_id = _get_applicant_id(request)
    log.info("Applicant query: id=%d, msg=%r", applicant_id, req.message[:100])
    history = [{"role": t.role, "content": t.content} for t in req.conversation_history]

    from core.applicant_orchestrator import handle_applicant_message
    result = await handle_applicant_message(applicant_id, req.message, history)
    return result


# ── Profile CRUD ─────────────────────────────────────────────

@app.get("/api/applicant/profile")
async def get_applicant_profile(request: Request):
    applicant_id = _get_applicant_id(request)
    from applicant.profile_manager import get_full_profile
    profile = get_full_profile(applicant_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    return profile


@app.put("/api/applicant/profile")
async def update_applicant_profile(req: ProfileUpdateRequest, request: Request):
    applicant_id = _get_applicant_id(request)
    from applicant.profile_manager import update_profile, update_profile_embedding
    fields = {k: v for k, v in req.model_dump().items() if v is not None}
    update_profile(applicant_id, fields)
    try:
        await update_profile_embedding(applicant_id)
    except Exception as e:
        log.warning("Embedding update failed: %s", e)
    return {"status": "updated"}


@app.post("/api/applicant/profile/confirm")
async def confirm_profile_data(req: ProfileConfirmRequest, request: Request):
    """Legacy endpoint — auto-save makes this mostly unused, but keep for compatibility."""
    applicant_id = _get_applicant_id(request)
    data = req.model_dump()
    log.info("CONFIRM DATA RECEIVED (legacy): %s", list(data.get("profile", {}).keys()))
    from applicant.profile_manager import save_extracted_data
    completion = save_extracted_data(applicant_id, data)
    return {"status": "saved", "completion": completion}


# ── Skills CRUD ──────────────────────────────────────────────

@app.post("/api/applicant/profile/skills")
async def add_applicant_skill(req: SkillRequest, request: Request):
    applicant_id = _get_applicant_id(request)
    from applicant.profile_manager import add_skill
    skill_id = add_skill(applicant_id, req.skill_name, req.proficiency_level, req.years_of_experience)
    return {"skill_id": skill_id}


@app.put("/api/applicant/profile/skills/{skill_id}")
async def update_applicant_skill(skill_id: int, req: SkillRequest, request: Request):
    _get_applicant_id(request)
    from applicant.profile_manager import update_skill
    update_skill(skill_id, req.model_dump())
    return {"status": "updated"}


@app.delete("/api/applicant/profile/skills/{skill_id}")
async def delete_applicant_skill(skill_id: int, request: Request):
    _get_applicant_id(request)
    from applicant.profile_manager import delete_skill
    delete_skill(skill_id)
    return {"status": "deleted"}


# ── Education CRUD ───────────────────────────────────────────

@app.post("/api/applicant/profile/education")
async def add_applicant_education(req: EducationRequest, request: Request):
    applicant_id = _get_applicant_id(request)
    from applicant.profile_manager import add_education
    edu_id = add_education(applicant_id, req.model_dump())
    return {"education_id": edu_id}


@app.put("/api/applicant/profile/education/{education_id}")
async def update_applicant_education(education_id: int, req: EducationRequest, request: Request):
    _get_applicant_id(request)
    from applicant.profile_manager import update_education
    update_education(education_id, req.model_dump())
    return {"status": "updated"}


@app.delete("/api/applicant/profile/education/{education_id}")
async def delete_applicant_education(education_id: int, request: Request):
    _get_applicant_id(request)
    from applicant.profile_manager import delete_education
    delete_education(education_id)
    return {"status": "deleted"}


# ── Experience CRUD ──────────────────────────────────────────

@app.post("/api/applicant/profile/experience")
async def add_applicant_experience(req: ExperienceRequest, request: Request):
    applicant_id = _get_applicant_id(request)
    from applicant.profile_manager import add_experience
    exp_id = add_experience(applicant_id, req.model_dump())
    return {"experience_id": exp_id}


@app.put("/api/applicant/profile/experience/{experience_id}")
async def update_applicant_experience(experience_id: int, req: ExperienceRequest, request: Request):
    _get_applicant_id(request)
    from applicant.profile_manager import update_experience
    update_experience(experience_id, req.model_dump())
    return {"status": "updated"}


@app.delete("/api/applicant/profile/experience/{experience_id}")
async def delete_applicant_experience(experience_id: int, request: Request):
    _get_applicant_id(request)
    from applicant.profile_manager import delete_experience
    delete_experience(experience_id)
    return {"status": "deleted"}


# ── Resume ───────────────────────────────────────────────────

@app.post("/api/applicant/profile/resume/upload")
async def upload_resume(request: Request, file: UploadFile = File(...)):
    applicant_id = _get_applicant_id(request)
    from applicant.resume_processor import handle_resume_upload
    result = await handle_resume_upload(applicant_id, file)
    return result


@app.get("/api/applicant/profile/resume/download")
async def download_resume(request: Request):
    applicant_id = _get_applicant_id(request)
    from applicant.resume_processor import get_resume_path
    path = get_resume_path(applicant_id)
    if not path or not Path(path).exists():
        raise HTTPException(status_code=404, detail="No resume found")
    return FileResponse(path, media_type="application/pdf",
                        headers={"Content-Disposition": f"inline; filename={Path(path).name}"})


@app.delete("/api/applicant/profile/resume")
async def delete_resume(request: Request):
    applicant_id = _get_applicant_id(request)
    from core.db import execute_write
    execute_write(
        "UPDATE applicant.applicant_profiles SET resume_file_path = NULL, resume_updated_at = NULL WHERE applicant_id = %s",
        [applicant_id],
    )
    return {"status": "deleted"}


# ── Profile Reset (for testing) ─────────────────────────────

@app.post("/api/applicant/profile/reset")
async def reset_applicant_profile(request: Request):
    """Reset profile to start fresh — deletes skills, education, experience, resets phase."""
    applicant_id = _get_applicant_id(request)
    from core.db import execute_write
    execute_write("DELETE FROM applicant.skills WHERE applicant_id = %s", [applicant_id])
    execute_write("DELETE FROM applicant.education WHERE applicant_id = %s", [applicant_id])
    execute_write("DELETE FROM applicant.experience WHERE applicant_id = %s", [applicant_id])
    execute_write(
        "UPDATE applicant.applicant_profiles SET "
        "desired_role = NULL, desired_department = NULL, experience_years = NULL, "
        "current_company = NULL, current_role = NULL, "
        "location_preference = '[]'::jsonb, salary_min = NULL, salary_max = NULL, "
        "job_type_preference = '[]'::jsonb, onboarding_phase = 1, profile_completion = 0, "
        "profile_embedding = NULL "
        "WHERE applicant_id = %s",
        [applicant_id],
    )
    log.info("Profile reset for applicant %d", applicant_id)
    return {"status": "reset", "message": "Profile cleared. Start fresh!"}


# ── Jobs & Applications ──────────────────────────────────────

@app.get("/api/applicant/jobs")
async def search_jobs(request: Request, q: str = ""):
    applicant_id = _get_applicant_id(request)
    from applicant.job_matcher import search_jobs
    jobs = search_jobs(applicant_id, query=q if q else None)
    return {"jobs": jobs}


@app.post("/api/applicant/jobs/{job_id}/apply")
async def apply_to_job(job_id: int, req: ApplicationSubmitRequest, request: Request):
    applicant_id = _get_applicant_id(request)
    from applicant.application_manager import has_existing_application, create_application, get_job_by_id
    from applicant.profile_manager import get_full_profile
    import json as _json

    if has_existing_application(applicant_id, job_id):
        raise HTTPException(status_code=409, detail="Already applied to this job")

    # ── Required skills gate (server-side enforcement) ──
    job = get_job_by_id(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    profile = get_full_profile(applicant_id)
    user_skills = set(s["skill_name"].lower() for s in profile.get("skills", []))
    required = job.get("required_skills", [])
    if isinstance(required, str):
        required = _json.loads(required)
    required_set = set(s.lower() for s in required)
    missing = required_set - user_skills

    if missing:
        raise HTTPException(
            status_code=403,
            detail=f"Missing required skills: {', '.join(sorted(missing))}. You must have all required skills to apply."
        )

    app_id = create_application(applicant_id, job_id, req.cover_letter, req.match_score)

    # Advance phase
    from core.db import execute_write
    execute_write(
        "UPDATE applicant.applicant_profiles SET onboarding_phase = GREATEST(onboarding_phase, 6) WHERE applicant_id = %s",
        [applicant_id],
    )
    return {"application_id": app_id, "status": "submitted"}


@app.post("/api/applicant/jobs/{job_id}/save")
async def save_job(job_id: int, request: Request):
    applicant_id = _get_applicant_id(request)
    from applicant.application_manager import save_job
    save_job(applicant_id, job_id)
    return {"status": "saved"}


@app.get("/api/applicant/applications")
async def get_applications(request: Request):
    applicant_id = _get_applicant_id(request)
    from applicant.application_manager import get_all_applications
    return {"applications": get_all_applications(applicant_id)}


@app.get("/api/applicant/applications/{application_id}")
async def get_application_detail(application_id: int, request: Request):
    _get_applicant_id(request)
    from applicant.application_manager import get_application
    app = get_application(application_id)
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")
    return app


@app.post("/api/applicant/applications/{application_id}/withdraw")
async def withdraw_application_endpoint(application_id: int, request: Request):
    applicant_id = _get_applicant_id(request)
    from applicant.application_manager import withdraw_application
    success = withdraw_application(application_id, applicant_id)
    if not success:
        raise HTTPException(status_code=400, detail="Cannot withdraw this application.")
    return {"status": "withdrawn", "application_id": application_id}


# ── File Downloads (shared) ──────────────────────────────────

@app.get("/api/download/{filename}")
async def download_file(filename: str):
    log.info("Download request: %s", filename)
    # Prevent path traversal
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    file_path = Path(config.generated_dir).resolve() / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")

    ext = file_path.suffix.lower()
    media_types = {
        ".pdf": "application/pdf",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }
    media_type = media_types.get(ext, "application/octet-stream")

    if ext == ".pdf":
        return FileResponse(
            str(file_path),
            media_type=media_type,
            headers={"Content-Disposition": f"inline; filename={filename}"},
        )
    return FileResponse(
        str(file_path),
        media_type=media_type,
        filename=filename,
    )


if __name__ == "__main__":
    import os
    import sys

    print("=" * 55)
    print("  Horizon Platform — Starting Server")
    print("=" * 55)

    # Check .env
    env_path = Path(__file__).parent / ".env"
    if not env_path.exists():
        print("\n[ERROR] .env file not found.")
        print("  Copy .env.example to .env, fill in your keys, then run: python setup.py")
        sys.exit(1)
    print("\n[OK] .env loaded")

    # Check PostgreSQL
    try:
        import psycopg2
        conn = psycopg2.connect(
            host=config.postgres_host, port=config.postgres_port,
            user=config.postgres_user, password=config.postgres_password,
            database=config.postgres_db,
        )
        conn.close()
        print(f"[OK] PostgreSQL connected ({config.postgres_db}@{config.postgres_host})")
    except Exception as e:
        print(f"\n[ERROR] Cannot connect to PostgreSQL: {e}")
        print("  Run 'python setup.py' first.")
        sys.exit(1)

    # Ensure directories
    Path(config.generated_dir).mkdir(exist_ok=True)
    Path(config.resume_upload_dir).mkdir(exist_ok=True)

    url = f"http://localhost:{config.server_port}"
    print(f"\n  Server URL:        {url}")
    print(f"  Landing page:      {url}/")
    print(f"  Company portal:    {url}/company")
    print(f"  Applicant portal:  {url}/applicant")
    print(f"\n  Press Ctrl+C to stop.\n")
    print("=" * 55)

    import uvicorn
    uvicorn.run(
        "service:app",
        host=config.server_host,
        port=config.server_port,
        log_level="info",
    )
