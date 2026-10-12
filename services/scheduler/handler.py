"""
Scheduler Lambda — weekly EventBridge rule.

Fires once a week (Sunday 18:00 SGT = Sunday 10:00 UTC, see
var.weekly_poll_cron_utc). Idempotently creates the SIT session for the
upcoming Tuesday, sends the poll, and schedules the exporter for 24h after the
session starts.

If a SIT session already exists for the upcoming week (admin ran /sendpoll
early, or /skipsession'd to cancel the week), this handler is a no-op, unless
that session never got its poll (a failed earlier attempt), which is resumed.
8 Wheel Clan sessions are manual-only and never suppress the SIT poll.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from shared import config, db, export_schedule, poll, telegram

log = logging.getLogger()
log.setLevel(logging.INFO)

_TUESDAY_WEEKDAY = 1  # Python: Mon=0 ... Sun=6


def lambda_handler(event: dict, context) -> dict:
    try:
        existing = db.get_upcoming_session(within_days=7)
        if existing is not None:
            if existing.get("skipped") or existing.get("poll_message_id"):
                log.info(
                    "Session %s already exists for %s — weekly cron is a no-op.",
                    existing["id"], existing["session_date"],
                )
                return {"status": "noop", "session_id": existing["id"]}
            # EventBridge invokes asynchronously and Lambda retries a failed
            # run. If an earlier attempt created the session but could not
            # post the poll, finish that session instead of skipping the week.
            log.warning("Session %s has no poll yet — resuming.", existing["id"])
            _send_poll(existing["id"])
            export_schedule.schedule_export(existing["id"])
            return {"status": "resumed", "session_id": existing["id"]}

        tz = ZoneInfo(config.TIMEZONE)
        now = datetime.now(tz)
        days_ahead = (_TUESDAY_WEEKDAY - now.weekday()) % 7
        session_date = (now + timedelta(days=days_ahead)).strftime("%Y-%m-%d")

        session_id = db.create_session(
            session_date=session_date,
            start_time=config.DEFAULT_SESSION_START,
            end_time=config.DEFAULT_SESSION_END,
            location=config.DEFAULT_SESSION_LOCATION,
            created_at=datetime.now(UTC).isoformat(),
            group_key='sit', group_name='SIT Inline Skate',
            poll_chat_id=config.attendance_poll_chat_id(),
            poll_topic_id=config.attendance_poll_topic_id(),
        )
        log.info("Created session %s for %s", session_id, session_date)

        _send_poll(session_id)
        export_schedule.schedule_export(session_id)
        return {"status": "created", "session_id": session_id, "session_date": session_date}
    except Exception:
        log.exception("Scheduler cron failed.")
        raise


def _send_poll(session_id: str) -> None:
    session = db.get_session(session_id)
    if session is None:
        return
    result = telegram.send_message(
        chat_id=session.get('poll_chat_id') or config.attendance_poll_chat_id(),
        text=poll.build_poll_text(session, []),
        reply_markup=poll.build_keyboard(session_id),
        message_thread_id=(session['poll_topic_id'] or None) if 'poll_topic_id' in session else config.attendance_poll_topic_id(),
    )
    db.set_poll_message_id(session_id, result["message_id"], session.get('poll_chat_id') or config.attendance_poll_chat_id())
