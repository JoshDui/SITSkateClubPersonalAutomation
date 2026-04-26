"""
Scheduler function — Timer Trigger.

Fires once a week per the NCRONTAB in function.json (Sunday 18:00 SGT
= Sunday 10:00 UTC). Idempotently creates a session for the upcoming
Tuesday, sends the poll, and enqueues a Storage Queue message with a
24h visibility_timeout for the exporter.

If a session already exists for the upcoming week (admin ran /sendpoll
early, or /skipsession'd to cancel the week), this handler is a no-op.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import azure.functions as func
from azure.identity import DefaultAzureCredential
from azure.storage.queue import QueueClient, TextBase64EncodePolicy

from shared import config, db, poll, telegram

log = logging.getLogger("scheduler")
log.setLevel(logging.INFO)

_TUESDAY_WEEKDAY = 1  # Python: Mon=0 ... Sun=6

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


def main(timer: func.TimerRequest) -> None:
    if timer.past_due:
        log.warning("Timer is past due — running anyway.")

    try:
        existing = db.get_upcoming_session(within_days=7)
        if existing is not None:
            log.info(
                "Session %s already exists for %s — weekly cron is a no-op.",
                existing["id"], existing["session_date"],
            )
            return

        tz = ZoneInfo(config.TIMEZONE)
        now = datetime.now(tz)
        days_ahead = (_TUESDAY_WEEKDAY - now.weekday()) % 7
        session_date = (now + timedelta(days=days_ahead)).strftime("%Y-%m-%d")

        session_id = db.create_session(
            session_date=session_date,
            start_time=config.DEFAULT_SESSION_START,
            end_time=config.DEFAULT_SESSION_END,
            location=config.DEFAULT_SESSION_LOCATION,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        log.info("Created session %s for %s", session_id, session_date)

        _send_poll(session_id)
        _enqueue_export(session_id)
    except Exception:
        log.exception("Scheduler cron failed.")
        raise


def _send_poll(session_id: str) -> None:
    session = db.get_session(session_id)
    if session is None:
        return
    result = telegram.send_message(
        chat_id=config.group_chat_id(),
        text=poll.build_poll_text(session, []),
        reply_markup=poll.build_keyboard(session_id),
    )
    db.set_poll_message_id(session_id, result["message_id"])


def _enqueue_export(session_id: str) -> None:
    """Enqueue the export job. Storage Queue's visibility_timeout hides the
    message until session_start + 24h, then the exporter (Queue Trigger)
    picks it up. Max visibility_timeout is 7 days — well above our 24h."""
    session = db.get_session(session_id)
    if session is None:
        return
    tz = ZoneInfo(config.TIMEZONE)
    start = datetime.strptime(
        f"{session['session_date']} {session['start_time']}", "%Y-%m-%d %H:%M",
    ).replace(tzinfo=tz)
    fire_at = start + timedelta(hours=24)
    now = datetime.now(tz)
    delay_seconds = max(60, int((fire_at - now).total_seconds()))

    payload = json.dumps({"session_id": session_id})
    _get_queue_client().send_message(payload, visibility_timeout=delay_seconds)
    log.info("Enqueued export message for %s, delay=%ds", session_id, delay_seconds)
