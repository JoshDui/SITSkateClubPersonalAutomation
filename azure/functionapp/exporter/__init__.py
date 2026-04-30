"""
Exporter function — Queue Trigger.

Fires when a Storage Queue message becomes visible (24h after session
start, enqueued by webhook /sendpoll, /setsession, or the weekly
scheduler). Reads session + responses from Cosmos DB, joins with
member profiles, builds a CSV, and uploads it to Blob Storage.

Sink rationale (see azure/PROGRESS.md "Attendance export pivot"):
the original design POSTed JSON to a Power Automate flow that wrote
rows into a SharePoint Excel file. That path is dead because the
Power Automate HTTP-request trigger requires a Premium licence the
SIT student tenant does not grant, and the Microsoft Graph
alternative is blocked by tenant directory permissions. We pivoted
to Blob CSV + a local openpyxl script (azure/scripts/build_attendance_report.py)
that aggregates the cloud-side artefacts into Attendance.xlsx on
demand.

Blob layout: ``{ATTENDANCE_CONTAINER}/{session_date}_{session_id}.csv``.
One CSV per session — the local report script aggregates them.

Idempotent by design (handles Storage Queue at-least-once delivery):
  - If session.skipped == 1 → no-op (admin ran /skipsession)
  - If session.exported == 1 → no-op (already uploaded, e.g. duplicate
    queue message from /setsession)
  - Otherwise: build CSV → upload to Blob → mark_session_exported.

CSV column order is deliberately frozen (see ``_CSV_HEADERS``) — the
local report script depends on it, and re-ordering would silently
break Attendance.xlsx generation.
"""
from __future__ import annotations

import csv
import io
import logging

import azure.functions as func
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient

from shared import config, db

log = logging.getLogger("exporter")
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

_blob_service_client: BlobServiceClient | None = None


def _get_blob_service_client() -> BlobServiceClient:
    """Module-level cache mirrors the scheduler's QueueClient pattern: one
    client + credential per cold start, reused across warm invocations."""
    global _blob_service_client
    if _blob_service_client is None:
        _blob_service_client = BlobServiceClient(
            account_url=config.BLOB_ACCOUNT_URL,
            credential=DefaultAzureCredential(),
        )
    return _blob_service_client


def main(msg: func.QueueMessage) -> None:
    body = msg.get_json()
    session_id = body.get("session_id") if isinstance(body, dict) else None
    if not session_id:
        log.error("Exporter invoked without session_id: %r", body)
        return

    session = db.get_session(session_id)
    if session is None:
        log.warning("Exporter: session %s not found — noop.", session_id)
        return
    if session.get("skipped") or session.get("exported"):
        log.info(
            "Exporter: session %s skipped=%s exported=%s — noop.",
            session_id, session.get("skipped"), session.get("exported"),
        )
        return

    responses = db.get_responses(session_id)
    attendees = _build_attendees(responses)

    csv_bytes = _build_csv(session, attendees)
    blob_name = f"{session['session_date']}_{session_id}.csv"
    _upload_csv_to_blob(blob_name, csv_bytes)
    db.mark_session_exported(session_id)

    log.info(
        "Exported session %s (%s) → %s: %d unique attendees across %d responses.",
        session_id, session["session_date"], blob_name,
        len({a["handle"] for a in attendees}), len(responses),
    )


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
            "full_name": member.get("full_name"),
            "student_id": member.get("student_id"),
            "full_course_name": member.get("full_course_name"),
            "cluster": member.get("cluster"),
            "year": member.get("year"),
            "is_sit_student": bool(member.get("is_sit_student", 0)),
            "categories": entry["categories"],
        })
    return enriched


def _build_csv(session: dict, attendees: list[dict]) -> bytes:
    """Render attendees to CSV with frozen column order. Returns UTF-8 bytes
    with a BOM so Excel on Windows opens it cleanly without an import wizard."""
    buf = io.StringIO(newline="")
    writer = csv.DictWriter(buf, fieldnames=_CSV_HEADERS, extrasaction="ignore")
    writer.writeheader()

    session_time = f"{session['start_time']} - {session['end_time']}"
    for a in attendees:
        writer.writerow({
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
        })
    # utf-8-sig prepends a BOM — Excel respects it and opens the file as UTF-8
    # without forcing the user through the legacy text-import wizard.
    return buf.getvalue().encode("utf-8-sig")


def _upload_csv_to_blob(blob_name: str, csv_bytes: bytes) -> None:
    """Upload the CSV under the configured attendance container. ``overwrite=True``
    is safe because the queue trigger is gated by ``mark_session_exported``;
    overwrite only fires when an admin manually resets ``exported=0`` to
    re-export, which is exactly the intended UX."""
    container = config.ATTENDANCE_CONTAINER
    blob_client = _get_blob_service_client().get_blob_client(
        container=container, blob=blob_name,
    )
    blob_client.upload_blob(
        csv_bytes,
        overwrite=True,
        content_type="text/csv; charset=utf-8",
    )
    log.info("Uploaded %s/%s (%d bytes)", container, blob_name, len(csv_bytes))
