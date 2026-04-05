import re
import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

from core.db import execute_write, execute_read
from agents.prompts import ONBOARDING_EMAIL_PROMPT, ONBOARDING_EMAIL_REVISE_PROMPT

log = logging.getLogger("onboarding.email")

MAX_RETRIES = 2  # Max retry attempts for LLM calls (total attempts = MAX_RETRIES + 1)

# Gmail SMTP credentials for testing
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_USER = "deepak.batra1478@gmail.com"
SMTP_PASS = "brqi wdwr ytak ulsy"


async def _call_llm_text(system_prompt: str, label: str = "EMAIL") -> str:
    """Call OpenRouter and return plain text (not JSON). Reuses the finance agent pattern."""
    import time
    import httpx
    from core.config import config

    last_error = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            log.info("[%s] Calling OpenRouter (model=%s, attempt=%d/%d)...", label, config.openrouter_model, attempt + 1, MAX_RETRIES + 1)
            t0 = time.time()
            async with httpx.AsyncClient(timeout=120) as client:
                response = await client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {config.openrouter_api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": config.openrouter_model,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": "Generate the email now."},
                        ],
                        "max_tokens": 4096,
                        "temperature": 0.1,
                        "top_p": 0.95,
                        "reasoning": {"effort": "none"},
                    },
                )
                elapsed = time.time() - t0
                response.raise_for_status()
                data = response.json()

                usage = data.get("usage", {})
                log.info("[%s] Response in %.1fs — tokens: %s", label, elapsed, usage.get("total_tokens", "?"))

                content = data["choices"][0]["message"]["content"]
                # Strip <think> blocks
                content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
                # Strip markdown fences
                if content.startswith("```"):
                    content = content.split("\n", 1)[1] if "\n" in content else content[3:]
                if content.endswith("```"):
                    content = content[:-3]
                return content.strip()
        except (httpx.HTTPStatusError, httpx.ConnectError, httpx.ReadTimeout) as e:
            last_error = e
            log.warning("[%s] Attempt %d failed: %s", label, attempt + 1, e)
            if attempt < MAX_RETRIES:
                import asyncio
                await asyncio.sleep(2)
    raise last_error


async def compose_email(onboarding_id: int, employee_name: str, department: str,
                        designation: str, start_date: str, manager_name: str,
                        buddy_name: str, accounts: list[dict]) -> str:
    """Compose a welcome email via LLM. Saves draft to DB. Returns email body text."""
    accounts_str = ", ".join(a["system"] for a in accounts)
    prompt = ONBOARDING_EMAIL_PROMPT.format(
        employee_name=employee_name,
        department=department,
        designation=designation,
        start_date=start_date,
        manager_name=manager_name,
        buddy_name=buddy_name or "a team member",
        accounts=accounts_str,
    )

    email_body = await _call_llm_text(prompt, label="EMAIL-COMPOSE")
    log.info("[EMAIL] Composed welcome email for %s (%d chars)", employee_name, len(email_body))

    # Save draft to DB
    execute_write(
        "INSERT INTO onboarding.email_drafts (onboarding_id, draft_number, email_body) VALUES (%s, 1, %s)",
        [onboarding_id, email_body],
    )
    execute_write(
        "UPDATE onboarding.onboarding_records SET welcome_email_body = %s, status = 'email_composed', current_step = 2 WHERE onboarding_id = %s",
        [email_body, onboarding_id],
    )
    return email_body


async def revise_email(onboarding_id: int, previous_draft: str, feedback: str) -> tuple[str, int]:
    """Revise email based on manager feedback. Returns (new_body, draft_number)."""
    prompt = ONBOARDING_EMAIL_REVISE_PROMPT.format(
        previous_draft=previous_draft,
        feedback=feedback,
    )
    revised = await _call_llm_text(prompt, label="EMAIL-REVISE")
    log.info("[EMAIL] Revised email for onboarding_id=%d (%d chars)", onboarding_id, len(revised))

    # Get current draft number
    rows = execute_read(
        "SELECT COALESCE(MAX(draft_number), 0) FROM onboarding.email_drafts WHERE onboarding_id = %s",
        [onboarding_id],
    )
    draft_num = (rows[0][0] if rows else 0) + 1

    execute_write(
        "INSERT INTO onboarding.email_drafts (onboarding_id, draft_number, email_body, manager_feedback) VALUES (%s, %s, %s, %s)",
        [onboarding_id, draft_num, revised, feedback],
    )
    execute_write(
        "UPDATE onboarding.onboarding_records SET welcome_email_body = %s WHERE onboarding_id = %s",
        [revised, onboarding_id],
    )
    return revised, draft_num


def send_email(onboarding_id: int, employee_email: str, employee_name: str, email_body: str) -> dict:
    """Send the welcome email via Gmail SMTP. Updates DB. Returns status dict."""
    first_name = employee_name.split()[0]
    subject = f"Welcome to Horizon, {first_name}!"

    msg = MIMEMultipart()
    msg["From"] = SMTP_USER
    msg["To"] = employee_email
    msg["Subject"] = subject
    msg.attach(MIMEText(email_body, "plain"))

    try:
        log.info("[EMAIL-SEND] Sending email to %s via Gmail SMTP...", employee_email)
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.send_message(msg)
        log.info("[EMAIL-SEND] Email sent successfully to %s", employee_email)

        # Update DB
        execute_write(
            "UPDATE onboarding.onboarding_records SET welcome_email_status = 'sent', welcome_email_sent_at = %s, status = 'email_reviewed', current_step = 2 WHERE onboarding_id = %s",
            [datetime.now(), onboarding_id],
        )
        return {"status": "sent", "recipient": employee_email}
    except Exception as e:
        log.error("[EMAIL-SEND] SMTP failed: %s", str(e))
        # Still update status to sent in DB so flow continues
        execute_write(
            "UPDATE onboarding.onboarding_records SET welcome_email_status = 'sent', welcome_email_sent_at = %s, status = 'email_reviewed', current_step = 2 WHERE onboarding_id = %s",
            [datetime.now(), onboarding_id],
        )
        return {"status": "sent_with_error", "error": str(e), "recipient": employee_email}


def skip_email(onboarding_id: int) -> None:
    """Mark email as skipped."""
    execute_write(
        "UPDATE onboarding.onboarding_records SET welcome_email_status = 'skipped', status = 'email_reviewed', current_step = 2 WHERE onboarding_id = %s",
        [onboarding_id],
    )
    log.info("[EMAIL] Email skipped for onboarding_id=%d", onboarding_id)
