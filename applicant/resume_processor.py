"""Resume Processor — Upload, multi-strategy extraction, LLM parsing.

Step A: Save raw PDF (always)
Step B: Extract text (pdfplumber → PyPDF2)
Step C: LLM parse into structured data
Step D: Return confirmation card (never save directly)
Step E: Graceful fallback if extraction fails
"""

import logging
import shutil
from pathlib import Path
from datetime import datetime

from core.config import config
from core.db import execute_write
from agents import applicant_agent

log = logging.getLogger("resume_processor")


def save_raw_resume(applicant_id: int, uploaded_file) -> str:
    """Save the original PDF to disk. Always succeeds or raises.
    The raw file is preserved regardless of parsing outcome."""
    resume_dir = Path(config.resume_upload_dir) / str(applicant_id)
    resume_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"resume_{timestamp}.pdf"
    filepath = resume_dir / filename

    # Save the raw bytes
    with open(filepath, "wb") as f:
        shutil.copyfileobj(uploaded_file.file, f)

    # Update DB immediately
    execute_write(
        "UPDATE applicant.applicant_profiles SET resume_file_path = %s, resume_updated_at = %s WHERE applicant_id = %s",
        [str(filepath), datetime.now(), applicant_id],
    )

    log.info("[RESUME] Raw PDF saved: %s (%d bytes)", filepath, filepath.stat().st_size)
    return str(filepath)


def extract_resume_text(filepath: str) -> tuple[str, str]:
    """Try multiple extraction strategies. Returns (text, method_used).
    Never raises — returns ("", "failed") on complete failure."""

    # Strategy 1: pdfplumber (best for text PDFs with tables/columns)
    try:
        import pdfplumber
        text = ""
        with pdfplumber.open(filepath) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text() or ""
                text += page_text + "\n"
        if len(text.strip()) > 100:
            log.info("[RESUME] pdfplumber extracted %d chars", len(text))
            return text.strip(), "pdfplumber"
    except Exception as e:
        log.warning("[RESUME] pdfplumber failed: %s", e)

    # Strategy 2: PyPDF2 (simpler, handles some PDFs pdfplumber can't)
    try:
        from PyPDF2 import PdfReader
        reader = PdfReader(filepath)
        text = ""
        for page in reader.pages:
            text += (page.extract_text() or "") + "\n"
        if len(text.strip()) > 100:
            log.info("[RESUME] PyPDF2 extracted %d chars", len(text))
            return text.strip(), "pypdf2"
    except Exception as e:
        log.warning("[RESUME] PyPDF2 failed: %s", e)

    # All strategies failed
    log.error("[RESUME] All extraction strategies failed for %s", filepath)
    return "", "failed"


async def handle_resume_upload(applicant_id: int, uploaded_file) -> dict:
    """Full resume processing pipeline. Returns a response dict (confirmation card or fallback)."""
    # Step A: Save raw PDF (always)
    filepath = save_raw_resume(applicant_id, uploaded_file)

    # Step B: Multi-strategy text extraction
    text, method = extract_resume_text(filepath)

    if text:
        # Step C: LLM parse into structured data
        try:
            parsed = await applicant_agent.parse_resume_text(text)
        except Exception as e:
            log.error("[RESUME] LLM parsing failed: %s", e)
            parsed = {}

        if parsed:
            # Step D: Return confirmation card
            return {
                "type": "applicant",
                "step": "confirm_data",
                "context": "resume_parsed",
                "message": "I extracted the following from your resume — please review and correct anything that looks off:",
                "resume_status": {
                    "raw_file_saved": True,
                    "file_path": Path(filepath).name,
                    "extraction_method": method,
                    "parsing_confidence": "high" if method == "pdfplumber" else "medium",
                },
                "extracted_data": {
                    "profile": {
                        k: {"value": v, "editable": True}
                        for k, v in {
                            "full_name": parsed.get("full_name"),
                            "desired_role": parsed.get("desired_role"),
                            "current_company": parsed.get("current_company"),
                            "current_role": parsed.get("current_role"),
                            "summary": parsed.get("summary"),
                        }.items() if v
                    },
                    "skills": {
                        "values": [
                            {**s, "editable": True, "removable": True}
                            for s in parsed.get("skills", [])
                        ],
                        "allow_add": True,
                    },
                    "education": {
                        "values": [
                            {**e, "editable": True, "removable": True}
                            for e in parsed.get("education", [])
                        ],
                        "allow_add": True,
                    },
                    "experience": {
                        "values": [
                            {**e, "editable": True, "removable": True}
                            for e in parsed.get("experience", [])
                        ],
                        "allow_add": True,
                    },
                },
                "actions": ["save", "cancel"],
            }

    # Step E: Extraction failed, fallback to questions
    return {
        "type": "applicant",
        "step": "resume_upload_result",
        "message": "I saved your resume, but I couldn't read it automatically. "
                   "No worries — your original file is safely stored and will be attached to all your applications. "
                   "Let me ask you a few questions to fill in your profile instead:",
        "resume_status": {
            "raw_file_saved": True,
            "file_path": Path(filepath).name,
            "extraction_method": "failed",
            "parsing_confidence": "none",
        },
        "questions": [
            {"id": "skills", "text": "What are your main technical skills?", "input_type": "text"},
            {"id": "experience", "text": "Where do you currently work, and what's your role?", "input_type": "text"},
            {"id": "education", "text": "What's your educational background?", "input_type": "text"},
        ],
    }


def get_resume_path(applicant_id: int) -> str | None:
    """Get the stored resume file path for an applicant."""
    from core.db import execute_read
    rows = execute_read(
        "SELECT resume_file_path FROM applicant.applicant_profiles WHERE applicant_id = %s",
        [applicant_id],
    )
    if rows and rows[0][0]:
        return rows[0][0]
    return None
