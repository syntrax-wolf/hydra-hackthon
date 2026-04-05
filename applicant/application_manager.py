"""Application Manager — Apply, timeline events, tracking dashboard."""

import json
import logging
import shutil
from datetime import datetime
from pathlib import Path
from core.db import execute_read, execute_write

log = logging.getLogger("application_manager")


def has_existing_application(applicant_id: int, job_id: int) -> bool:
    """Check if applicant already applied to this job."""
    rows = execute_read(
        "SELECT application_id FROM applicant.applications WHERE applicant_id = %s AND job_id = %s",
        [applicant_id, job_id],
    )
    return len(rows) > 0


def create_application(applicant_id: int, job_id: int, cover_letter: str,
                       match_score: int = 0) -> int:
    """Create a new application record + snapshot resume."""
    # Snapshot resume
    resume_snapshot = None
    resume_rows = execute_read(
        "SELECT resume_file_path FROM applicant.applicant_profiles WHERE applicant_id = %s",
        [applicant_id],
    )
    if resume_rows and resume_rows[0][0]:
        src = Path(resume_rows[0][0])
        if src.exists():
            snapshot_dir = src.parent / "snapshots"
            snapshot_dir.mkdir(exist_ok=True)
            dest = snapshot_dir / f"app_{job_id}_{src.name}"
            shutil.copy2(src, dest)
            resume_snapshot = str(dest)

    execute_write(
        "INSERT INTO applicant.applications (applicant_id, job_id, cover_letter, "
        "resume_snapshot_path, match_score, status) VALUES (%s, %s, %s, %s, %s, 'submitted')",
        [applicant_id, job_id, cover_letter, resume_snapshot, match_score],
    )

    rows = execute_read(
        "SELECT application_id FROM applicant.applications WHERE applicant_id = %s AND job_id = %s",
        [applicant_id, job_id],
    )
    app_id = rows[0][0] if rows else 0

    # Add timeline event
    add_timeline_event(app_id, "submitted", "Application submitted")

    log.info("[APPLICATION] Created application %d (applicant=%d, job=%d)",
             app_id, applicant_id, job_id)
    return app_id


def add_timeline_event(application_id: int, event_type: str, details: str) -> None:
    """Add a timeline event to an application."""
    execute_write(
        "INSERT INTO applicant.application_timeline (application_id, event_type, details) "
        "VALUES (%s, %s, %s)",
        [application_id, event_type, details],
    )


def get_application(application_id: int) -> dict | None:
    """Fetch a single application with its timeline."""
    rows = execute_read(
        "SELECT a.application_id, a.applicant_id, a.job_id, a.status, a.cover_letter, "
        "a.resume_snapshot_path, a.match_score, a.applied_at, a.updated_at, "
        "j.title, j.company "
        "FROM applicant.applications a "
        "JOIN applicant.job_postings j ON j.job_id = a.job_id "
        "WHERE a.application_id = %s",
        [application_id],
    )
    if not rows:
        return None
    r = rows[0]
    app = {
        "application_id": r[0], "applicant_id": r[1], "job_id": r[2],
        "status": r[3], "cover_letter": r[4], "resume_snapshot_path": r[5],
        "match_score": r[6],
        "applied_at": r[7].isoformat() if r[7] else None,
        "updated_at": r[8].isoformat() if r[8] else None,
        "job_title": r[9], "company": r[10],
    }

    # Get timeline
    timeline_rows = execute_read(
        "SELECT event_id, event_type, details, created_at "
        "FROM applicant.application_timeline WHERE application_id = %s ORDER BY created_at",
        [application_id],
    )
    app["timeline"] = [
        {"event_id": t[0], "event_type": t[1], "details": t[2],
         "created_at": t[3].isoformat() if t[3] else None}
        for t in timeline_rows
    ]
    return app


def get_all_applications(applicant_id: int) -> list[dict]:
    """Fetch all applications for an applicant."""
    rows = execute_read(
        "SELECT a.application_id, a.job_id, a.status, a.match_score, a.applied_at, "
        "j.title, j.company "
        "FROM applicant.applications a "
        "JOIN applicant.job_postings j ON j.job_id = a.job_id "
        "WHERE a.applicant_id = %s ORDER BY a.applied_at DESC",
        [applicant_id],
    )
    return [
        {"application_id": r[0], "job_id": r[1], "status": r[2], "match_score": r[3],
         "applied_at": r[4].isoformat() if r[4] else None,
         "job_title": r[5], "company": r[6]}
        for r in rows
    ]


def get_latest_application(applicant_id: int) -> dict | None:
    """Get the most recent application."""
    rows = execute_read(
        "SELECT application_id, job_id, status FROM applicant.applications "
        "WHERE applicant_id = %s ORDER BY applied_at DESC LIMIT 1",
        [applicant_id],
    )
    if not rows:
        return None
    return {"application_id": rows[0][0], "job_id": rows[0][1], "status": rows[0][2]}


def get_job_by_id(job_id: int) -> dict | None:
    """Re-export from job_matcher for convenience."""
    from applicant.job_matcher import get_job_by_id as _get
    return _get(job_id)


def save_job(applicant_id: int, job_id: int) -> None:
    """Bookmark a job."""
    execute_write(
        "INSERT INTO applicant.saved_jobs (applicant_id, job_id) VALUES (%s, %s) "
        "ON CONFLICT (applicant_id, job_id) DO NOTHING",
        [applicant_id, job_id],
    )


def withdraw_application(application_id: int, applicant_id: int) -> bool:
    """Withdraw an application. Returns True if successful."""
    rows = execute_read(
        "SELECT status FROM applicant.applications WHERE application_id = %s AND applicant_id = %s",
        [application_id, applicant_id],
    )
    if not rows:
        return False
    status = rows[0][0]
    if status in ("withdrawn", "rejected", "accepted"):
        return False

    execute_write(
        "UPDATE applicant.applications SET status = 'withdrawn', updated_at = now() "
        "WHERE application_id = %s AND applicant_id = %s",
        [application_id, applicant_id],
    )
    add_timeline_event(application_id, "withdrawn", "Application withdrawn by applicant")
    log.info("[APPLICATION] Withdrawn application %d (applicant=%d)", application_id, applicant_id)
    return True


def get_saved_jobs(applicant_id: int) -> list[dict]:
    """Get all saved/bookmarked jobs."""
    rows = execute_read(
        "SELECT s.job_id, j.title, j.company, s.saved_at "
        "FROM applicant.saved_jobs s "
        "JOIN applicant.job_postings j ON j.job_id = s.job_id "
        "WHERE s.applicant_id = %s ORDER BY s.saved_at DESC",
        [applicant_id],
    )
    return [
        {"job_id": r[0], "title": r[1], "company": r[2],
         "saved_at": r[3].isoformat() if r[3] else None}
        for r in rows
    ]
