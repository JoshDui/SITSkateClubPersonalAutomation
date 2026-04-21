"""
Exporter Lambda.

Invoked by EventBridge Scheduler at session_start + 24h. Reads the session +
responses from DynamoDB, joins with member profiles, builds the attendance
payload, and POSTs it to the Power Automate HTTP trigger.

Payload schema matches PLAN.md §Attendance Export (Power Automate).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import httpx

from shared import config, db

log = logging.getLogger()
log.setLevel(logging.INFO)

_HTTP_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=30.0, pool=5.0)


def lambda_handler(event: dict, context) -> dict:
    session_id = event.get("session_id")
    if not session_id:
        log.error("Exporter invoked without session_id: %r", event)
        return {"status": "error", "reason": "missing_session_id"}

    session = db.get_session(session_id)
    if session is None:
        log.warning("Exporter: session %s not found — noop.", session_id)
        return {"status": "skipped", "reason": "session_missing"}
    if session.get("skipped") or session.get("exported"):
        log.info(
            "Exporter: session %s skipped=%s exported=%s — noop.",
            session_id, session.get("skipped"), session.get("exported"),
        )
        return {"status": "skipped", "reason": "already_handled"}

    responses = db.get_responses(session_id)
    attendees = _build_attendees(responses)
    payload = {
        "session_date": session["session_date"],
        "session_time": f"{session['start_time']} - {session['end_time']}",
        "session_location": session["location"],
        "exported_at": datetime.now(ZoneInfo(config.TIMEZONE)).isoformat(),
        "attendees": attendees,
    }

    _post_to_power_automate(payload)
    db.mark_session_exported(session_id)

    log.info(
        "Exported session %s (%s): %d unique attendees across %d responses.",
        session_id, session["session_date"],
        len({a["handle"] for a in attendees}), len(responses),
    )
    return {"status": "exported", "session_id": session_id, "attendee_count": len(attendees)}


def _build_attendees(responses: list[dict]) -> list[dict]:
    """Collapse responses (one row per tap) into one entry per telegram user,
    with a `categories` list. Joined with member profile data where available."""
    by_user: dict[int, dict] = {}
    for r in responses:
        tg_id = r["telegram_id"]
        entry = by_user.setdefault(
            tg_id,
            {
                "telegram_id": tg_id,
                "handle": r.get("username"),
                "first_name": r.get("first_name"),
                "categories": [],
            },
        )
        cat = r.get("category")
        if cat and cat not in entry["categories"]:
            entry["categories"].append(cat)

    # Enrich with member profile data (name, student_id, course, etc.)
    enriched = []
    for entry in by_user.values():
        handle = entry.get("handle")
        member = db.get_member(handle) if handle else None
        if member:
            enriched.append({
                "handle": handle,
                "full_name": member.get("full_name"),
                "student_id": member.get("student_id"),
                "full_course_name": member.get("full_course_name"),
                "cluster": member.get("cluster"),
                "year": member.get("year"),
                "is_sit_student": bool(member.get("is_sit_student", 0)),
                "categories": entry["categories"],
            })
        else:
            enriched.append({
                "handle": handle,
                "full_name": None,
                "student_id": None,
                "full_course_name": None,
                "cluster": None,
                "year": None,
                "is_sit_student": False,
                "categories": entry["categories"],
            })
    return enriched


def _post_to_power_automate(payload: dict) -> None:
    url = config.power_automate_url()
    if "PLACEHOLDER" in url or "example.com" in url:
        log.warning("POWER_AUTOMATE_URL is still a placeholder — skipping HTTP POST. Payload would be: %s", payload)
        return
    with httpx.Client(timeout=_HTTP_TIMEOUT) as client:
        r = client.post(url, json=payload)
    r.raise_for_status()
    log.info("Power Automate POST → %s (%d bytes)", r.status_code, len(r.content or b""))
