"""One-off EventBridge Scheduler job that fires the exporter 24h after a
session starts.

One schedule per session, named after the session ID: rescheduling
(/setsession) updates the same schedule rather than adding a second one, and
ActionAfterCompletion=DELETE removes it once it has fired. The exporter is
idempotent regardless (it checks the session's skipped/exported/deleted state).
API: docs.aws.amazon.com/scheduler/latest/APIReference/API_CreateSchedule.html
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import boto3

from shared import config, db

log = logging.getLogger(__name__)

_client = None


def _scheduler():
    global _client
    if _client is None:
        _client = boto3.client("scheduler")
    return _client


def schedule_name(session_id: str) -> str:
    return f"{config.SCHEDULE_NAME_PREFIX}{session_id}"


def schedule_export(session_id: str) -> None:
    session = db.get_session(session_id)
    if session is None:
        return
    tz = ZoneInfo(config.TIMEZONE)
    start = datetime.strptime(
        f"{session['session_date']} {session['start_time']}", "%Y-%m-%d %H:%M",
    ).replace(tzinfo=tz)
    fire_at = start + timedelta(hours=24)
    now = datetime.now(tz)
    # at() expressions must be in the future.
    if fire_at <= now:
        fire_at = now + timedelta(seconds=60)

    params = {
        "Name": schedule_name(session_id),
        "GroupName": config.SCHEDULER_GROUP,
        "ScheduleExpression": f"at({fire_at.strftime('%Y-%m-%dT%H:%M:%S')})",
        "ScheduleExpressionTimezone": config.TIMEZONE,
        "FlexibleTimeWindow": {"Mode": "OFF"},
        "Target": {
            "Arn": config.EXPORTER_LAMBDA_ARN,
            "RoleArn": config.SCHEDULER_TARGET_ROLE,
            "Input": json.dumps({"session_id": session_id}),
        },
        "ActionAfterCompletion": "DELETE",
    }
    client = _scheduler()
    try:
        client.create_schedule(**params)
        log.info("Created export schedule %s for %s", params["Name"], fire_at.isoformat())
    except client.exceptions.ConflictException:
        client.update_schedule(**params)
        log.info("Updated export schedule %s for %s", params["Name"], fire_at.isoformat())
