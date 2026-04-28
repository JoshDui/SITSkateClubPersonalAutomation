"""
Telegram webhook function — HTTP Trigger.

Invoked via the Function App's anonymous HTTP endpoint on every Telegram
update (message or callback_query). Authenticated by the secret token
Telegram includes in the `X-Telegram-Bot-Api-Secret-Token` header.

Responds 200 OK quickly — any user-visible work runs inline. Telegram
retries if we take >60s or return a non-2xx.

Differences from the AWS Lambda port:
  - boto3.client("scheduler") + EventBridge Scheduler at()  →
    azure.storage.queue.QueueClient.send_message(visibility_timeout=…)
  - /skipsession + /setsession can no longer cancel/update an in-flight
    queued export message. Instead they flip Cosmos state, and the
    exporter checks `skipped`/`exported` before exporting (idempotent).
"""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import azure.functions as func
from azure.identity import DefaultAzureCredential
from azure.storage.queue import QueueClient, TextBase64EncodePolicy

from shared import config, db, importer, poll, telegram

log = logging.getLogger("webhook")
log.setLevel(logging.INFO)

# ULID alphabet (Crockford base32, exactly 26 chars). callback_query.data is
# user-controlled and flows directly into Cosmos OData filter strings, so we
# validate session_id matches the ULID shape before any DB call.
_ULID_RE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")

# Storage Queue client cached at module level for warm-container reuse.
# TextBase64EncodePolicy matches the Functions Queue Trigger default
# (which expects base64-encoded messages).
_queue_client: QueueClient | None = None


def _get_queue_client() -> QueueClient:
    global _queue_client
    if _queue_client is None:
        _queue_client = QueueClient(
            account_url=config.QUEUE_ACCOUNT_URL,
            queue_name=config.EXPORT_QUEUE_NAME,
            credential=DefaultAzureCredential(),
            message_encode_policy=TextBase64EncodePolicy(),
        )
    return _queue_client


def main(req: func.HttpRequest) -> func.HttpResponse:
    try:
        if not _verify_secret(req):
            log.warning("Rejected webhook with invalid secret token.")
            return _resp(401, "unauthorized")

        raw = req.get_body() or b"{}"
        body = json.loads(raw)
        log.info("update_id=%s keys=%s", body.get("update_id"), list(body.keys()))

        if "callback_query" in body:
            _handle_callback(body["callback_query"])
        elif "message" in body:
            _handle_message(body["message"])

        return _resp(200, "ok")
    except Exception:
        log.exception("Webhook handler failed.")
        # Return 200 anyway — a 5xx makes Telegram retry, which usually makes
        # things worse. We've logged the failure; App Insights alarm will fire.
        return _resp(200, "error-logged")


# ── Auth + response helpers ─────────────────────────────────────────────────

def _verify_secret(req: func.HttpRequest) -> bool:
    got = req.headers.get("x-telegram-bot-api-secret-token")
    return got == config.webhook_secret()


def _resp(code: int, body: str) -> func.HttpResponse:
    return func.HttpResponse(body, status_code=code, mimetype="text/plain")


# ── Message dispatch (commands) ─────────────────────────────────────────────

def _handle_message(msg: dict) -> None:
    text = (msg.get("text") or "").strip()
    user = msg.get("from") or {}
    chat = msg.get("chat") or {}
    user_id = user.get("id")

    if not text.startswith("/"):
        return

    cmd, _, args_str = text.partition(" ")
    cmd = cmd.split("@", 1)[0].lstrip("/").lower()
    args = args_str.split() if args_str else []

    if user_id not in config.admin_ids():
        telegram.send_message(chat["id"], "⛔ Admin only.")
        return

    handler = _COMMAND_HANDLERS.get(cmd)
    if handler is None:
        return
    handler(msg, args)


def _cmd_sendpoll(msg: dict, args: list[str]) -> None:
    now = datetime.now(ZoneInfo(config.TIMEZONE))
    days_ahead = (1 - now.weekday()) % 7  # 1 == Tuesday
    session_date = (now + timedelta(days=days_ahead)).strftime("%Y-%m-%d")

    session_id = db.create_session(
        session_date=session_date,
        start_time=config.DEFAULT_SESSION_START,
        end_time=config.DEFAULT_SESSION_END,
        location=config.DEFAULT_SESSION_LOCATION,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    _send_poll(session_id)
    _enqueue_export(session_id)

    telegram.send_message(
        msg["chat"]["id"],
        f"✅ Poll sent for session on {session_date}. (Session ID: {session_id})",
    )


def _cmd_skipsession(msg: dict, args: list[str]) -> None:
    session = db.get_latest_session()
    if session is None:
        telegram.send_message(msg["chat"]["id"], "No sessions found.")
        return
    db.mark_session_skipped(session["id"])
    # Cannot retract an already-queued export message; exporter checks the
    # skipped flag and no-ops.
    telegram.send_message(
        msg["chat"]["id"],
        f"⏭ Session {session['id']} ({session['session_date']}) marked as skipped.",
    )


def _cmd_setsession(msg: dict, args: list[str]) -> None:
    if len(args) < 4:
        telegram.send_message(
            msg["chat"]["id"],
            "Usage: /setsession YYYY-MM-DD START_HH:MM END_HH:MM Location\n"
            "Example: /setsession 2026-04-29 18:30 21:30 SIT @ Punggol Coast",
        )
        return

    session_date, start_time, end_time, *loc_parts = args
    location = " ".join(loc_parts)

    try:
        datetime.strptime(session_date, "%Y-%m-%d")
        datetime.strptime(start_time, "%H:%M")
        datetime.strptime(end_time, "%H:%M")
    except ValueError:
        telegram.send_message(
            msg["chat"]["id"],
            "❌ Invalid format. Date must be YYYY-MM-DD, times must be HH:MM.",
        )
        return

    session = db.get_latest_session()
    if session is None:
        telegram.send_message(
            msg["chat"]["id"],
            "No session to update. Use /sendpoll to create one first.",
        )
        return

    db.update_session_details(session["id"], session_date, start_time, end_time, location)
    # Enqueue a NEW export message at the new fire time. The original message
    # may still fire at its original time; exporter checks `exported` flag
    # and the second invocation no-ops.
    _enqueue_export(session["id"])

    telegram.send_message(
        msg["chat"]["id"],
        f"✅ Session {session['id']} updated:\n"
        f"  Date:     {session_date}\n"
        f"  Time:     {start_time} – {end_time}\n"
        f"  Location: {location}",
    )


def _cmd_summary(msg: dict, args: list[str]) -> None:
    session = db.get_latest_session()
    if session is None:
        telegram.send_message(msg["chat"]["id"], "No sessions found.")
        return

    labels = poll.category_labels()
    lines = [
        f"\U0001f4cb Summary — {session['session_date']} ({session['location']})",
        f"Session ID: {session['id']}",
        "",
    ]
    total_ids: set[int] = set()
    for cat_key in poll.CATEGORY_KEYS:
        responses = db.get_responses_by_category(session["id"], cat_key)
        names = [r.get("first_name", "") for r in responses]
        total_ids.update(r["telegram_id"] for r in responses)
        lines.append(f"{labels[cat_key]} ({len(names)}):")
        if names:
            lines.extend(f"  • {n}" for n in names)
        else:
            lines.append("  (none)")
        lines.append("")
    lines.append(f"\U0001f465 {len(total_ids)} unique respondents")
    telegram.send_message(msg["chat"]["id"], "\n".join(lines))


def _cmd_importmembers(msg: dict, args: list[str]) -> None:
    doc = msg.get("document")
    if doc is None:
        telegram.send_message(
            msg["chat"]["id"],
            "\U0001f4ce Please send the Excel file (.xlsx) as a document attachment "
            "together with /importmembers.",
        )
        return
    if not (doc.get("file_name") or "").endswith(".xlsx"):
        telegram.send_message(msg["chat"]["id"], "❌ File must be a .xlsx Excel file.")
        return

    telegram.send_message(msg["chat"]["id"], "⏳ Downloading and importing...")

    file_info = telegram.get_file(doc["file_id"])
    file_path = file_info["file_path"]

    # Azure Functions Linux Consumption provides /tmp via tempfile defaults.
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        telegram.download_file(file_path, tmp_path)
        counts = importer.import_from_file(tmp_path)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    total_in_db = db.count_members()
    telegram.send_message(
        msg["chat"]["id"],
        f"✅ Import complete!\n"
        f"  New:       {counts['new']}\n"
        f"  Updated:   {counts['updated']}\n"
        f"  Unchanged: {counts['unchanged']}\n"
        f"  Skipped:   {counts['skipped']} (missing name or handle)\n"
        f"  Rows read: {counts['total']}\n\n"
        f"Total members in database: {total_in_db}",
    )


_COMMAND_HANDLERS = {
    "sendpoll": _cmd_sendpoll,
    "skipsession": _cmd_skipsession,
    "setsession": _cmd_setsession,
    "summary": _cmd_summary,
    "importmembers": _cmd_importmembers,
}


# ── Callback query (button tap) ─────────────────────────────────────────────

def _handle_callback(cq: dict) -> None:
    data = cq.get("data") or ""
    telegram.answer_callback_query(cq["id"])  # clear the "loading" spinner quickly

    parts = data.split(":")
    if len(parts) != 3 or parts[0] != "vote":
        return
    session_id, category = parts[1], parts[2]

    # Both fields flow into Cosmos OData filter strings. Reject anything that
    # doesn't match the expected shape, otherwise a crafted callback could
    # break out of the filter literal.
    if not _ULID_RE.match(session_id) or category not in poll.CATEGORY_KEYS:
        log.warning("Rejected callback with invalid session_id/category: %r", data)
        return

    user = cq.get("from") or {}
    user_id = user.get("id")
    if user_id is None:
        log.warning("Callback missing from.id — dropping (would corrupt shared 'None' row).")
        return
    now_iso = datetime.now(timezone.utc).isoformat()

    db.toggle_response(
        session_id=session_id,
        telegram_id=user_id,
        username=user.get("username"),
        first_name=user.get("first_name") or user.get("username") or str(user_id),
        category=category,
        responded_at=now_iso,
    )

    session = db.get_session(session_id)
    if session is None:
        return

    responses = db.get_responses(session_id)
    message = cq.get("message") or {}
    chat_id = (message.get("chat") or {}).get("id")
    message_id = message.get("message_id")
    if chat_id is None or message_id is None:
        return

    telegram.edit_message_text(
        chat_id=chat_id,
        message_id=message_id,
        text=poll.build_poll_text(session, responses),
        reply_markup=poll.build_keyboard(session_id),
    )


# ── Poll send + export-job orchestration ────────────────────────────────────

def _send_poll(session_id: str) -> None:
    """Send a fresh poll to the group and persist the message_id for later edits."""
    session = db.get_session(session_id)
    if session is None:
        return
    result = telegram.send_message(
        chat_id=config.group_chat_id(),
        text=poll.build_poll_text(session, []),
        reply_markup=poll.build_keyboard(session_id),
        message_thread_id=config.group_topic_id(),
    )
    db.set_poll_message_id(session_id, result["message_id"])


def _enqueue_export(session_id: str) -> None:
    """Schedule the export by enqueueing a Storage Queue message with a
    visibility_timeout that hides it until 24h after session start.

    Storage Queue's max visibility_timeout is 7 days (604800s) — well above
    our 24h need. The exporter (Queue Trigger) wakes up when the message
    becomes visible.

    Unlike AWS EventBridge Scheduler, queued messages cannot be cancelled
    or updated in-place. /skipsession sets Cosmos `skipped=1`; /setsession
    enqueues a new message at the new time. The exporter checks both
    `skipped` and `exported` flags and no-ops on either."""
    session = db.get_session(session_id)
    if session is None:
        return
    tz = ZoneInfo(config.TIMEZONE)
    start = datetime.strptime(
        f"{session['session_date']} {session['start_time']}", "%Y-%m-%d %H:%M",
    ).replace(tzinfo=tz)
    fire_at = start + timedelta(hours=24)
    now = datetime.now(tz)
    # Storage Queue rejects visibility_timeout > 604800 (7 days). /setsession
    # to a date >7d out would otherwise throw silently and lose the export job.
    delay_seconds = min(max(60, int((fire_at - now).total_seconds())), 604800)

    payload = json.dumps({"session_id": session_id})
    _get_queue_client().send_message(payload, visibility_timeout=delay_seconds)
    log.info("Enqueued export message for %s, delay=%ds", session_id, delay_seconds)
