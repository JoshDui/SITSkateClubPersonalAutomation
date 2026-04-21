"""
Scheduler Lambda.

Invoked once a week by an EventBridge Rule (Sunday 18:00 SGT = Sunday 10:00
UTC). Idempotently creates a session for the upcoming Tuesday, sends the
poll, and registers an EventBridge Scheduler one-off to fire the exporter
24h after the session's start time.

If a session already exists for the upcoming week (admin ran /sendpoll
early, or /skipsession'd to cancel the week), this handler is a no-op.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import boto3

from shared import config, db, poll, telegram

log = logging.getLogger()
log.setLevel(logging.INFO)

_scheduler = boto3.client("scheduler")
_lambda = boto3.client("lambda")

_TUESDAY_WEEKDAY = 1  # Python: Mon=0 ... Sun=6


def lambda_handler(event: dict, context) -> dict:
    try:
        existing = db.get_upcoming_session(within_days=7)
        if existing is not None:
            log.info(
                "Session %s already exists for %s — weekly cron is a no-op.",
                existing["id"], existing["session_date"],
            )
            return {"status": "skipped", "reason": "session_exists", "session_id": existing["id"]}

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
        _schedule_export(session_id)

        return {"status": "created", "session_id": session_id, "session_date": session_date}
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


def _schedule_export(session_id: str) -> None:
    session = db.get_session(session_id)
    if session is None:
        return
    tz = ZoneInfo(config.TIMEZONE)
    start = datetime.strptime(
        f"{session['session_date']} {session['start_time']}", "%Y-%m-%d %H:%M",
    ).replace(tzinfo=tz)
    fire_at = start + timedelta(hours=24)
    now = datetime.now(tz)
    if fire_at <= now:
        fire_at = now + timedelta(seconds=60)

    at_expr = f"at({fire_at.strftime('%Y-%m-%dT%H:%M:%S')})"
    name = f"{os.environ['SCHEDULE_NAME_PREFIX']}{session_id}"

    params = {
        "Name": name,
        "GroupName": os.environ["SCHEDULER_GROUP"],
        "ScheduleExpression": at_expr,
        "ScheduleExpressionTimezone": config.TIMEZONE,
        "FlexibleTimeWindow": {"Mode": "OFF"},
        "Target": {
            "Arn": os.environ["EXPORTER_LAMBDA_ARN"],
            "RoleArn": os.environ["SCHEDULER_TARGET_ROLE"],
            "Input": json.dumps({"session_id": session_id}),
        },
        "ActionAfterCompletion": "DELETE",
    }

    try:
        _scheduler.create_schedule(**params)
        log.info("Created export schedule %s for %s", name, fire_at.isoformat())
    except _scheduler.exceptions.ConflictException:
        _scheduler.update_schedule(**params)
        log.info("Updated export schedule %s for %s", name, fire_at.isoformat())
