"""
Exporter Lambda — invoked by a one-off EventBridge Scheduler job 24h after a
session starts (created by /sendpoll, /setsession, or the weekly scheduler).

Reads the session + responses, joins with member profiles, builds a CSV, and
sends it as a Telegram document to every admin's private chat. This replaces
the Azure version's Blob upload: the reports reach the committee directly and
need no storage service.

Idempotent by design (EventBridge Scheduler / Lambda async retries):
  - session missing or deleted → no-op
  - session.skipped == 1 → no-op (admin ran /skipsession)
  - session.exported == 1 → no-op (already delivered)
  - Otherwise: build CSV → DM admins → mark_session_exported.

Special events (kind='event') get their own CSV with the registration details
(name, student number or partial NRIC/FIN, year of study, skates). Those
details are then deleted from the database, and also whenever an event is
skipped, deleted or already exported, so a retry finishes the purge.

Delivery succeeds if at least one admin receives the file; failures for other
admins (e.g. they never opened a chat with the bot) are logged. If nobody
receives it, the handler raises so Lambda retries.

CSV column order is deliberately frozen (see ``_CSV_HEADERS``) — the local
report script depends on it, and re-ordering would silently break
Attendance.xlsx generation.
"""
from __future__ import annotations

import csv
import io
import logging

from shared import booking_store, config, db, telegram

log = logging.getLogger()
log.setLevel(logging.INFO)

# Frozen — the local report script reads these in this order.
_CSV_HEADERS = [
    "session_date",
    "session_time",
    "session_location",
    "handle",
    "full_name",
    "student_id",
    "full_course_name",
    "cluster",
    "year",
    "is_sit_student",
    "categories",
]

# Frozen order for special-event exports. The first eight columns follow the
# OSE Student Training Attendance Form (Apr-OSE-FRM-042) so rows paste
# straight into it; the rest are club/event specifics.
_EVENT_CSV_HEADERS = [
    "S/N",
    "Full Name",
    "Student ID",
    "Cluster",
    "Degree Programme",
    "Year of Study",
    "Type of Participation",
    "Remarks",
    "Category",
    "Partial NRIC/FIN",
    "Telegram Handle",
    "Rental Requested EU",
    "Rental Allocated EU",
    "Guards",
    "Event",
    "Event Date",
    "Event Time",
    "Event Location",
    "Source",
]

# Spreadsheet apps execute cells starting with these (CSV formula injection).
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _safe(value):
    """Neutralise user-controlled text that a spreadsheet would run as a formula."""
    if isinstance(value, str) and value.startswith(_FORMULA_PREFIXES):
        return "'" + value
    return value


def lambda_handler(event: dict, context) -> dict:
    session_id = event.get("session_id") if isinstance(event, dict) else None
    if not session_id:
        log.error("Exporter invoked without session_id: %r", event)
        return {"status": "error", "reason": "missing_session_id"}

    session = db.get_session(session_id, include_deleted=True)
    if session is None:
        log.warning("Exporter: session %s not found — noop.", session_id)
        return {"status": "skipped", "reason": "session_missing"}
    is_event = session.get("kind") == "event"
    if session.get("deleted") or session.get("skipped") or session.get("exported"):
        log.info(
            "Exporter: session %s deleted=%s skipped=%s exported=%s — noop.",
            session_id, bool(session.get("deleted")), session.get("skipped"), session.get("exported"),
        )
        if is_event:
            booking_store.purge_event_pii(session_id)
        return {"status": "skipped", "reason": "skipped_or_exported"}

    if is_event:
        state = booking_store.read(session_id) or {"people": {}}
        rows = _build_event_rows(session, state)
        csv_bytes = _render_csv(_EVENT_CSV_HEADERS, rows)
        attendee_count = len(rows)
        filename = f"{session['session_date']}_event_{session_id}.csv"
    else:
        responses = db.get_responses(session_id)
        attendees = _build_attendees(responses)
        csv_bytes = _build_csv(session, attendees)
        attendee_count = len(attendees)
        filename = f"{session['session_date']}_{session_id}.csv"

    # Recheck after building in case an admin deleted the session meanwhile.
    # This does not retract a report that was already delivered.
    current_session = db.get_session(session_id)
    if current_session is None or current_session.get('skipped') or current_session.get('exported'):
        log.info('Export cancelled after state recheck for session %s', session_id)
        if is_event:
            booking_store.purge_event_pii(session_id)
        return {"status": "skipped", "reason": "state_changed"}

    delivered = _send_to_admins(session, filename, csv_bytes, attendee_count)
    db.mark_session_exported(session_id)
    if is_event:
        booking_store.purge_event_pii(session_id)
    log.info(
        "Exported session %s (%s) → %s to %d admin(s): %d attendees.",
        session_id, session["session_date"], filename, delivered, attendee_count,
    )
    return {"status": "exported", "session_id": session_id, "delivered": delivered}


def _send_to_admins(session: dict, filename: str, csv_bytes: bytes, attendee_count: int) -> int:
    caption = (f"Attendance: {session.get('title') or session.get('group_name', 'SIT Inline Skate')} / "
               f"{session['session_date']} ({attendee_count} attendees)")
    delivered = 0
    for admin_id in sorted(config.admin_ids()):
        try:
            telegram.send_document(admin_id, filename, csv_bytes, caption=caption)
            delivered += 1
        except Exception:
            log.exception("Could not send %s to admin %s", filename, admin_id)
    if delivered == 0:
        raise RuntimeError(f"Attendance export {filename} reached no admins")
    return delivered


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

    enriched = []
    for entry in by_user.values():
        handle = entry.get("handle")
        member = (db.get_member(handle) if handle else None) or {}
        enriched.append({
            "handle": handle,
            "full_name": member.get("full_name") or entry.get("first_name"),
            "student_id": member.get("student_id"),
            "full_course_name": member.get("full_course_name"),
            "cluster": member.get("cluster"),
            "year": member.get("year"),
            "is_sit_student": any(cat in ('sit_student', 'rental_skates') for cat in entry['categories']),
            "categories": entry["categories"],
        })
    return enriched


def _build_event_rows(session: dict, state: dict) -> list[dict]:
    """One row per registrant, SIT students first, in registration order."""
    rows = []
    # Telegram usernames are verified by Telegram; web-typed ones are not.
    telegram_handles = {(p.get("username") or "").lower() for p in state["people"].values()
                        if p.get("attendance") and (p.get("event_form") or {}).get("source") != "web"}
    for person in state["people"].values():
        category = {"sit_student": "SIT", "non_sit": "Alumni / External"}.get(person.get("attendance"))
        if not category:
            continue
        form = person.get("event_form") or {}
        rental = person.get("rental") or {}
        sit = category == "SIT"
        remarks = "Programme not listed" if sit and form and not form.get("programme") else ""
        if form.get("source") == "web" and (person.get("username") or "") in telegram_handles - {""}:
            remarks = "; ".join(filter(None, [remarks, "Web-entered Telegram username matches a "
                                                       "Telegram registrant: please check"]))
        rows.append({
            "Full Name": form.get("name") or person.get("first_name") or "",
            "Student ID": form.get("student_no") or "",
            "Cluster": form.get("cluster") or "",
            "Degree Programme": form.get("programme") or "",
            "Year of Study": form.get("year") or "",  # Blank for alumni / external participants.
            "Type of Participation": "Member",
            "Remarks": remarks,
            "Category": category,
            "Partial NRIC/FIN": form.get("masked_id") or "",
            "Telegram Handle": person.get("username") or "",
            "Rental Requested EU": rental.get("requested") or "",
            "Rental Allocated EU": rental.get("allocated") or "",
            "Guards": ("Yes" if rental.get("guards") else "No") if rental else "",
            "Event": session.get("title") or "",
            "Event Date": session["session_date"],
            "Event Time": f"{session['start_time']} - {session['end_time']}",
            "Event Location": session["location"],
            "Source": "Web" if form.get("source") == "web" else "Telegram",
            "_order": (not sit, person.get("last_update", 0)),
        })
    rows.sort(key=lambda row: row.pop("_order"))
    for number, row in enumerate(rows, start=1):
        row["S/N"] = number
    return rows


def _render_csv(headers: list[str], rows: list[dict]) -> bytes:
    buf = io.StringIO(newline="")
    writer = csv.DictWriter(buf, fieldnames=headers, extrasaction="ignore")
    writer.writeheader()
    writer.writerows({k: _safe(v) for k, v in row.items()} for row in rows)
    return buf.getvalue().encode("utf-8-sig")


def _build_csv(session: dict, attendees: list[dict]) -> bytes:
    """Render attendees to CSV with frozen column order. Returns UTF-8 bytes
    with a BOM so Excel on Windows opens it cleanly without an import wizard."""
    buf = io.StringIO(newline="")
    writer = csv.DictWriter(buf, fieldnames=_CSV_HEADERS, extrasaction="ignore")
    writer.writeheader()

    session_time = f"{session['start_time']} - {session['end_time']}"
    for a in attendees:
        writer.writerow({k: _safe(v) for k, v in {
            "session_date": session["session_date"],
            "session_time": session_time,
            "session_location": session["location"],
            "handle": a.get("handle") or "",
            "full_name": a.get("full_name") or "",
            "student_id": a.get("student_id") or "",
            "full_course_name": a.get("full_course_name") or "",
            "cluster": a.get("cluster") or "",
            "year": a.get("year") or "",
            "is_sit_student": "Yes" if a.get("is_sit_student") else "No",
            "categories": ", ".join(a.get("categories") or []),
        }.items()})
    # utf-8-sig prepends a BOM — Excel respects it and opens the file as UTF-8
    # without forcing the user through the legacy text-import wizard.
    return buf.getvalue().encode("utf-8-sig")
