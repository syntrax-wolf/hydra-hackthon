import logging
from datetime import date, datetime, timedelta, time
from core.db import execute_read, execute_write
import json

log = logging.getLogger("onboarding.calendar")

DAYS_OF_WEEK = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def find_available_slots(manager_email: str, start_date, num_slots: int = 3) -> list[dict]:
    """Find free 30-minute slots on/after start_date from the manager's schedule."""
    # Ensure start_date is a date object
    if isinstance(start_date, str):
        start_date = date.fromisoformat(start_date)
    elif not isinstance(start_date, date):
        start_date = date.fromisoformat(str(start_date))

    # Get manager's schedule
    rows = execute_read(
        "SELECT day_of_week, start_time, end_time, is_available, block_label "
        "FROM onboarding.manager_schedule WHERE manager_email = %s ORDER BY day_of_week, start_time",
        [manager_email],
    )

    if not rows:
        log.warning("[CALENDAR] No schedule found for %s, generating default slots", manager_email)
        slots = []
        current = start_date
        for i in range(3):
            while current.weekday() >= 5:  # skip weekends
                current += timedelta(days=1)
            slots.append({
                "index": i,
                "date": current.isoformat(),
                "day": DAYS_OF_WEEK[current.weekday()],
                "start": "10:00",
                "end": "10:30",
            })
            current += timedelta(days=1)
        return slots

    # Build schedule lookup: day_of_week -> list of free blocks
    free_blocks = {}
    for day_of_week, start_time, end_time, is_available, block_label in rows:
        if is_available:
            if day_of_week not in free_blocks:
                free_blocks[day_of_week] = []
            free_blocks[day_of_week].append((start_time, end_time))

    slots = []
    current = start_date
    max_days = 20  # search up to 20 calendar days

    for _ in range(max_days):
        if len(slots) >= num_slots:
            break

        weekday = current.weekday()  # 0=Monday
        if weekday >= 5:  # skip weekends
            current += timedelta(days=1)
            continue

        day_blocks = free_blocks.get(weekday, [])
        for block_start, block_end in day_blocks:
            if len(slots) >= num_slots:
                break

            # Calculate block duration in minutes
            start_minutes = block_start.hour * 60 + block_start.minute
            end_minutes = block_end.hour * 60 + block_end.minute
            duration = end_minutes - start_minutes

            if duration >= 30:
                # Take the first 30-minute chunk from each free block
                slot_end_minutes = start_minutes + 30
                slot_end_h = slot_end_minutes // 60
                slot_end_m = slot_end_minutes % 60
                slots.append({
                    "index": len(slots),
                    "date": current.isoformat(),
                    "day": DAYS_OF_WEEK[current.weekday()],
                    "start": f"{block_start.hour:02d}:{block_start.minute:02d}",
                    "end": f"{slot_end_h:02d}:{slot_end_m:02d}",
                })

        current += timedelta(days=1)

    log.info("[CALENDAR] Found %d available slots for %s starting from %s", len(slots), manager_email, start_date)
    return slots


def confirm_slot(onboarding_id: int, slot: dict, employee_name: str, manager_name: str, manager_email: str) -> None:
    """Confirm a meeting slot and update the onboarding record."""
    meeting_dt = datetime.fromisoformat(f"{slot['date']}T{slot['start']}:00")
    attendees = [
        {"name": manager_name, "email": manager_email},
        {"name": employee_name, "email": ""},
    ]

    execute_write(
        "UPDATE onboarding.onboarding_records SET kickoff_meeting_time = %s, kickoff_meeting_attendees = %s, "
        "status = 'scheduled', current_step = 3 WHERE onboarding_id = %s",
        [meeting_dt, json.dumps(attendees), onboarding_id],
    )
    log.info("[CALENDAR] Meeting confirmed: onboarding_id=%d at %s", onboarding_id, meeting_dt)
