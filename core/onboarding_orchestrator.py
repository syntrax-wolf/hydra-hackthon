import logging
import json
import time
from datetime import datetime

from core.db import execute_write, execute_read
from agents.onboarding_agent import extract_employee_info, check_missing_fields
from onboarding.provisioner import provision_accounts
from onboarding.email_composer import compose_email, revise_email, send_email, skip_email
from onboarding.calendar_scheduler import find_available_slots, confirm_slot
from onboarding.doc_generator import generate_onboarding_doc

log = logging.getLogger("onboarding_orchestrator")

ONBOARDING_KEYWORDS = [
    "onboard", "onboarding", "new hire", "new employee", "new joiner",
    "selected", "joining", "start onboarding", "begin onboarding",
    "hired", "recruitment", "new recruit", "welcome aboard",
]


def is_onboarding_request(message: str) -> bool:
    """Keyword-based routing — no LLM call needed."""
    lower = message.lower()
    return any(kw in lower for kw in ONBOARDING_KEYWORDS)


def _get_onboarding_record(onboarding_id: int) -> dict:
    """Fetch an onboarding record by ID."""
    rows = execute_read(
        "SELECT onboarding_id, employee_name, employee_email, department, designation, region, "
        "manager_name, manager_email, buddy_name, buddy_email, start_date, status, current_step, "
        "accounts_provisioned, welcome_email_body, welcome_email_status, kickoff_meeting_time, "
        "onboarding_doc_path "
        "FROM onboarding.onboarding_records WHERE onboarding_id = %s",
        [onboarding_id],
    )
    if not rows:
        return None
    r = rows[0]
    return {
        "onboarding_id": r[0], "employee_name": r[1], "employee_email": r[2],
        "department": r[3], "designation": r[4], "region": r[5],
        "manager_name": r[6], "manager_email": r[7], "buddy_name": r[8],
        "buddy_email": r[9], "start_date": r[10].isoformat() if r[10] else None,
        "status": r[11], "current_step": r[12],
        "accounts_provisioned": r[13] if isinstance(r[13], list) else json.loads(r[13] or "[]"),
        "welcome_email_body": r[14], "welcome_email_status": r[15],
        "kickoff_meeting_time": r[16].isoformat() if r[16] else None,
        "onboarding_doc_path": r[17],
    }


def _search_pending_employees(name: str, department: str = None) -> list[dict]:
    """Search onboarding.onboarding_records for pending employees matching the name."""
    # Build ILIKE pattern: "Rohan Das" → "%rohan%das%"
    words = name.strip().lower().split()
    pattern = "%" + "%".join(words) + "%"

    if department:
        rows = execute_read(
            "SELECT onboarding_id, employee_name, employee_email, department, designation, region, start_date "
            "FROM onboarding.onboarding_records "
            "WHERE status = 'pending' AND LOWER(employee_name) LIKE %s AND department = %s "
            "ORDER BY employee_name",
            [pattern, department],
        )
    else:
        rows = execute_read(
            "SELECT onboarding_id, employee_name, employee_email, department, designation, region, start_date "
            "FROM onboarding.onboarding_records "
            "WHERE status = 'pending' AND LOWER(employee_name) LIKE %s "
            "ORDER BY employee_name",
            [pattern],
        )

    results = []
    for r in rows:
        results.append({
            "onboarding_id": r[0],
            "employee_name": r[1],
            "employee_email": r[2],
            "department": r[3],
            "designation": r[4],
            "region": r[5],
            "start_date": r[6].isoformat() if r[6] else None,
        })
    log.info("[SEARCH] Found %d pending employees matching '%s'", len(results), name)
    return results


def _find_manager_for_department(department: str) -> dict:
    """Find a Lead/Director in the same department from hr.employees."""
    rows = execute_read(
        "SELECT full_name, email_address FROM hr.employees "
        "WHERE department = %s AND designation IN ('Lead', 'Principal', 'Director') AND is_active = true "
        "ORDER BY employee_id LIMIT 1",
        [department],
    )
    if rows:
        return {"name": rows[0][0], "email": rows[0][1]}
    # Fallback: any Lead/Director
    rows = execute_read(
        "SELECT full_name, email_address FROM hr.employees "
        "WHERE designation IN ('Lead', 'Principal', 'Director') AND is_active = true "
        "ORDER BY employee_id LIMIT 1",
        [],
    )
    if rows:
        return {"name": rows[0][0], "email": rows[0][1]}
    return {"name": "HR Manager", "email": "hr@horizon.com"}


async def _start_onboarding_pipeline(onboarding_id: int, start_time: float) -> dict:
    """Run provisioning + email composition for a matched onboarding record."""
    record = _get_onboarding_record(onboarding_id)
    if not record:
        return {"type": "onboarding", "step": "error", "error": "Onboarding record not found"}

    employee_name = record["employee_name"]
    department = record["department"]
    designation = record["designation"]
    employee_email = record["employee_email"]
    start_date = record["start_date"]

    # Find manager from hr.employees
    manager = _find_manager_for_department(department)
    manager_name = manager["name"]
    manager_email = manager["email"]

    # Find a buddy — pick another employee from same department
    buddy_rows = execute_read(
        "SELECT full_name, email_address FROM hr.employees "
        "WHERE department = %s AND is_active = true AND designation NOT IN ('Lead', 'Principal', 'Director') "
        "ORDER BY employee_id LIMIT 1",
        [department],
    )
    buddy_name = buddy_rows[0][0] if buddy_rows else None
    buddy_email = buddy_rows[0][1] if buddy_rows else None

    # Update record with manager + buddy info
    execute_write(
        "UPDATE onboarding.onboarding_records SET manager_name = %s, manager_email = %s, "
        "buddy_name = %s, buddy_email = %s WHERE onboarding_id = %s",
        [manager_name, manager_email, buddy_name, buddy_email, onboarding_id],
    )

    # Step 1: Provision accounts
    accounts = provision_accounts(onboarding_id, employee_name, employee_email, department)
    log.info("[ONBOARDING] Provisioned %d accounts for %s", len(accounts), employee_name)

    # Step 2: Compose welcome email via LLM
    email_body = await compose_email(
        onboarding_id, employee_name, department, designation,
        start_date, manager_name, buddy_name or "a team member", accounts,
    )

    elapsed = int((time.time() - start_time) * 1000)
    log.info("[ONBOARDING] Pipeline steps 1-2 complete in %dms. Awaiting email review.", elapsed)

    return {
        "type": "onboarding",
        "step": "email_review",
        "onboarding_id": onboarding_id,
        "employee_name": employee_name,
        "employee_email": employee_email,
        "department": department,
        "designation": designation,
        "start_date": start_date,
        "manager_name": manager_name,
        "accounts_provisioned": [a["system"] for a in accounts],
        "email_draft": {
            "subject": f"Welcome to Horizon, {employee_name.split()[0]}!",
            "body": email_body,
        },
        "draft_number": 1,
        "time_ms": elapsed,
    }


async def handle_onboarding_message(message: str, conversation_history: list[dict] = None) -> dict:
    """Main entry point: extract name → search DB → route accordingly."""
    start = time.time()
    log.info("=" * 60)
    log.info("[ONBOARDING] Processing onboarding request")
    log.info("[ONBOARDING] Message: %s", message[:200])

    try:
        # Step 0: Extract employee name via LLM
        info = await extract_employee_info(message)
        missing = check_missing_fields(info)

        if missing:
            elapsed = int((time.time() - start) * 1000)
            return {
                "type": "onboarding",
                "step": "info_needed",
                "missing_fields": missing,
                "extracted": info,
                "message": "I need at least the employee's name to start onboarding. Please provide their name.",
                "time_ms": elapsed,
            }

        employee_name = info["employee_name"]
        department = info.get("department")  # optional, helps narrow search

        # Step 1: Search pending onboarding records in DB
        candidates = _search_pending_employees(employee_name, department)

        if len(candidates) == 0:
            elapsed = int((time.time() - start) * 1000)
            log.info("[ONBOARDING] No pending employees found matching '%s'", employee_name)
            return {
                "type": "onboarding",
                "step": "not_found",
                "message": f"No pending onboarding record found for \"{employee_name}\". Please check the spelling or ask HR to create the onboarding record first.",
                "searched_name": employee_name,
                "time_ms": elapsed,
            }

        if len(candidates) == 1:
            # Exact match — proceed directly
            match = candidates[0]
            log.info("[ONBOARDING] Single match: %s (id=%d)", match["employee_name"], match["onboarding_id"])
            return await _start_onboarding_pipeline(match["onboarding_id"], start)

        # Multiple matches — ask manager to pick
        elapsed = int((time.time() - start) * 1000)
        log.info("[ONBOARDING] Multiple matches (%d) for '%s', asking manager to pick", len(candidates), employee_name)
        return {
            "type": "onboarding",
            "step": "pick_employee",
            "message": f"I found {len(candidates)} pending employees matching \"{employee_name}\". Which one do you mean?",
            "candidates": candidates,
            "time_ms": elapsed,
        }

    except Exception as e:
        elapsed = int((time.time() - start) * 1000)
        log.error("[ONBOARDING] Error: %s", str(e)[:300])
        return {
            "type": "onboarding",
            "step": "error",
            "error": str(e)[:500],
            "time_ms": elapsed,
        }


async def handle_employee_selection(onboarding_id: int) -> dict:
    """Manager picked a specific employee from the disambiguation list."""
    start = time.time()
    log.info("[ONBOARDING] Employee selected: onboarding_id=%d", onboarding_id)
    try:
        return await _start_onboarding_pipeline(onboarding_id, start)
    except Exception as e:
        log.error("[ONBOARDING-SELECT] Error: %s", str(e)[:300])
        return {"type": "onboarding", "step": "error", "error": str(e)[:500]}


async def handle_email_action(onboarding_id: int, action: str, feedback: str = None) -> dict:
    """Handle manager's decision on the welcome email."""
    start = time.time()
    record = _get_onboarding_record(onboarding_id)
    if not record:
        return {"type": "onboarding", "step": "error", "error": "Onboarding record not found"}

    try:
        if action == "revise":
            previous_draft = record["welcome_email_body"] or ""
            revised_body, draft_num = await revise_email(onboarding_id, previous_draft, feedback or "")
            elapsed = int((time.time() - start) * 1000)

            note = ""
            if draft_num >= 3:
                note = "You can also type the email directly in the text box below."

            return {
                "type": "onboarding",
                "step": "email_revised",
                "onboarding_id": onboarding_id,
                "email_draft": {
                    "subject": f"Welcome to Horizon, {record['employee_name'].split()[0]}!",
                    "body": revised_body,
                },
                "draft_number": draft_num,
                "note": note,
                "time_ms": elapsed,
            }

        if action == "send":
            send_result = send_email(
                onboarding_id,
                record["employee_email"],
                record["employee_name"],
                record["welcome_email_body"] or "",
            )
        elif action == "skip":
            skip_email(onboarding_id)
            send_result = {"status": "skipped"}

        # After send or skip, proceed to calendar scheduling
        slots = find_available_slots(
            record["manager_email"],
            record["start_date"],
        )

        elapsed = int((time.time() - start) * 1000)
        step_name = "email_sent" if action == "send" else "email_skipped"

        return {
            "type": "onboarding",
            "step": "calendar_slots",
            "onboarding_id": onboarding_id,
            "email_status": step_name,
            "send_result": send_result if action == "send" else None,
            "employee_name": record["employee_name"],
            "slots": slots,
            "time_ms": elapsed,
        }

    except Exception as e:
        log.error("[ONBOARDING-EMAIL] Error: %s", str(e)[:300])
        return {"type": "onboarding", "step": "error", "error": str(e)[:500]}


def _insert_into_hr_employees(record: dict) -> int:
    """Insert the onboarded employee into hr.employees. Returns new employee_id."""
    result = execute_write(
        "INSERT INTO hr.employees (full_name, email_address, department, designation, "
        "office_location, date_of_joining, is_active) "
        "VALUES (%s, %s, %s, %s, %s, %s, true) RETURNING employee_id",
        [
            record["employee_name"],
            record["employee_email"],
            record["department"],
            record["designation"],
            record.get("region"),
            record["start_date"],
        ],
    )
    emp_id = result[0] if result else None
    log.info("[HR-INSERT] Inserted %s into hr.employees (employee_id=%s)", record["employee_name"], emp_id)
    return emp_id


async def handle_slot_selection(onboarding_id: int, slot_index: int) -> dict:
    """Manager picks a meeting slot → confirm + generate doc + insert into hr.employees + mark complete."""
    start = time.time()
    record = _get_onboarding_record(onboarding_id)
    if not record:
        return {"type": "onboarding", "step": "error", "error": "Onboarding record not found"}

    try:
        slots = find_available_slots(record["manager_email"], record["start_date"])
        if slot_index < 0 or slot_index >= len(slots):
            return {"type": "onboarding", "step": "error", "error": "Invalid slot index"}

        selected_slot = slots[slot_index]

        # Confirm the slot
        confirm_slot(
            onboarding_id, selected_slot,
            record["employee_name"], record["manager_name"], record["manager_email"],
        )

        meeting_time_str = f"{selected_slot['day']}, {selected_slot['date']} at {selected_slot['start']}"

        # Step 4: Generate onboarding PDF
        accounts = record["accounts_provisioned"]
        if isinstance(accounts, str):
            accounts = json.loads(accounts)

        doc_filename = await generate_onboarding_doc(
            onboarding_id,
            record["employee_name"],
            record["department"],
            record["designation"],
            record.get("region", ""),
            record["start_date"],
            record["manager_name"],
            record.get("buddy_name", "TBD"),
            accounts,
            meeting_time_str,
        )

        # Step 5: Insert into hr.employees
        emp_id = _insert_into_hr_employees(record)

        # Step 6: Mark onboarding complete
        execute_write(
            "UPDATE onboarding.onboarding_records SET status = 'complete', current_step = 6, completed_at = %s WHERE onboarding_id = %s",
            [datetime.now(), onboarding_id],
        )

        elapsed = int((time.time() - start) * 1000)
        log.info("[ONBOARDING] Complete! onboarding_id=%d, employee_id=%s, time=%dms", onboarding_id, emp_id, elapsed)

        return {
            "type": "onboarding",
            "step": "complete",
            "onboarding_id": onboarding_id,
            "employee_name": record["employee_name"],
            "department": record["department"],
            "meeting_time": meeting_time_str,
            "employee_id": emp_id,
            "doc_file": {
                "name": doc_filename,
                "download_url": f"/api/download/{doc_filename}" if doc_filename else None,
            } if doc_filename else None,
            "time_ms": elapsed,
        }

    except Exception as e:
        log.error("[ONBOARDING-SLOT] Error: %s", str(e)[:300])
        return {"type": "onboarding", "step": "error", "error": str(e)[:500]}


async def get_dashboard() -> dict:
    """Return all onboarding records for the dashboard view."""
    rows = execute_read(
        "SELECT onboarding_id, employee_name, department, status, current_step, created_at, start_date "
        "FROM onboarding.onboarding_records ORDER BY created_at DESC",
        [],
    )
    records = []
    for r in rows:
        records.append({
            "onboarding_id": r[0],
            "employee_name": r[1],
            "department": r[2],
            "status": r[3],
            "current_step": r[4],
            "created_at": r[5].isoformat() if r[5] else None,
            "start_date": r[6].isoformat() if r[6] else None,
        })

    total = len(records)
    pending = sum(1 for r in records if r["status"] == "pending")
    in_progress = sum(1 for r in records if r["status"] not in ("pending", "complete", "failed"))
    completed = sum(1 for r in records if r["status"] == "complete")
    failed = sum(1 for r in records if r["status"] == "failed")

    return {
        "records": records,
        "stats": {
            "total": total,
            "pending": pending,
            "in_progress": in_progress,
            "completed": completed,
            "failed": failed,
        },
    }
