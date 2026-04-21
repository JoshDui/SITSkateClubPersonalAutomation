"""
DynamoDB access layer for the skate bot.

Tables (defined in infra/terraform/dynamodb.tf):

  members   — PK=username
  sessions  — PK=id (ULID); GSI by_date (PK=gsi1_pk='SESSION', SK=session_date)
  responses — PK=session_id, SK='{category}#{telegram_id}'

All public function signatures mirror the Phase 3 sqlite3 db.py so that
handler code ports cleanly. Return types are `dict`s instead of sqlite3.Row,
so callers use `row["key"]` just like before.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import boto3
from boto3.dynamodb.conditions import Attr, Key

from shared import config

_ddb = boto3.resource("dynamodb")
_members = _ddb.Table(config.TABLE_MEMBERS)
_sessions = _ddb.Table(config.TABLE_SESSIONS)
_responses = _ddb.Table(config.TABLE_RESPONSES)

_SESSION_GSI_PK = "SESSION"


def _unwrap(v: Any) -> Any:
    """Decimal → int/float; everything else unchanged."""
    if isinstance(v, Decimal):
        return int(v) if v % 1 == 0 else float(v)
    if isinstance(v, list):
        return [_unwrap(x) for x in v]
    if isinstance(v, dict):
        return {k: _unwrap(x) for k, x in v.items()}
    return v


def _to_dict(item: dict | None) -> dict | None:
    if item is None:
        return None
    return _unwrap(item)


def _now_iso_utc() -> str:
    return datetime.now(ZoneInfo("UTC")).isoformat()


def _new_ulid() -> str:
    """26-char Crockford-base32 ULID. Import python-ulid if available, else
    fall back to timestamp+random hex (loses ULID spec but still sortable)."""
    try:
        from ulid import ULID
        return str(ULID())
    except ImportError:
        return f"{int(datetime.now(ZoneInfo('UTC')).timestamp() * 1000):013d}{secrets.token_hex(8)}"


# ── Members ──────────────────────────────────────────────────────────────────

def upsert_member(
    username: str,
    full_name: str,
    is_sit_student: bool,
    student_id: str | None,
    full_course_name: str | None,
    cluster: str | None,
    year: str | None,
    imported_at: str,
) -> str:
    """Returns 'new', 'updated', or 'unchanged'."""
    username = username.lstrip("@").lower()
    new_item = {
        "username": username,
        "full_name": full_name,
        "is_sit_student": 1 if is_sit_student else 0,
        "student_id": student_id,
        "full_course_name": full_course_name,
        "cluster": cluster,
        "year": year,
        "imported_at": imported_at,
    }
    new_item = {k: v for k, v in new_item.items() if v is not None}

    existing = _members.get_item(Key={"username": username}).get("Item")
    if existing is None:
        _members.put_item(Item=new_item)
        return "new"

    existing = _to_dict(existing)
    changed = any(
        existing.get(k) != new_item.get(k)
        for k in ("full_name", "is_sit_student", "student_id", "full_course_name", "cluster", "year")
    )
    if changed:
        _members.put_item(Item=new_item)
        return "updated"
    return "unchanged"


def get_member(username: str) -> dict | None:
    username = username.lstrip("@").lower()
    item = _members.get_item(Key={"username": username}).get("Item")
    return _to_dict(item)


def count_members() -> int:
    return _members.scan(Select="COUNT")["Count"]


# ── Sessions ─────────────────────────────────────────────────────────────────

def create_session(
    session_date: str,
    start_time: str,
    end_time: str,
    location: str,
    created_at: str | None = None,
) -> str:
    """Returns the new session's id (ULID)."""
    session_id = _new_ulid()
    item = {
        "id": session_id,
        "gsi1_pk": _SESSION_GSI_PK,
        "session_date": session_date,
        "start_time": start_time,
        "end_time": end_time,
        "location": location,
        "skipped": 0,
        "exported": 0,
        "created_at": created_at or _now_iso_utc(),
    }
    _sessions.put_item(Item=item)
    return session_id


def get_session(session_id: str) -> dict | None:
    item = _sessions.get_item(Key={"id": session_id}).get("Item")
    return _to_dict(item)


def get_latest_session() -> dict | None:
    """Most recent session by session_date. Targeted by /setsession, /skipsession,
    /summary. Note: Phase 3 used `ORDER BY id DESC` (insertion order); here we
    use `ORDER BY session_date DESC` via the GSI — close enough for the admin
    UX, and actually more intuitive ('latest session' = latest scheduled date)."""
    resp = _sessions.query(
        IndexName="by_date",
        KeyConditionExpression=Key("gsi1_pk").eq(_SESSION_GSI_PK),
        ScanIndexForward=False,
        Limit=1,
    )
    items = resp.get("Items", [])
    return _to_dict(items[0]) if items else None


def set_poll_message_id(session_id: str, message_id: int) -> None:
    _sessions.update_item(
        Key={"id": session_id},
        UpdateExpression="SET poll_message_id = :m",
        ExpressionAttributeValues={":m": message_id},
    )


def mark_session_skipped(session_id: str) -> None:
    _sessions.update_item(
        Key={"id": session_id},
        UpdateExpression="SET skipped = :v",
        ExpressionAttributeValues={":v": 1},
    )


def mark_session_exported(session_id: str) -> None:
    _sessions.update_item(
        Key={"id": session_id},
        UpdateExpression="SET exported = :v",
        ExpressionAttributeValues={":v": 1},
    )


def update_session_details(
    session_id: str,
    session_date: str,
    start_time: str,
    end_time: str,
    location: str,
) -> None:
    _sessions.update_item(
        Key={"id": session_id},
        UpdateExpression="SET session_date = :sd, start_time = :st, end_time = :et, #loc = :l",
        ExpressionAttributeNames={"#loc": "location"},
        ExpressionAttributeValues={
            ":sd": session_date,
            ":st": start_time,
            ":et": end_time,
            ":l": location,
        },
    )


def list_unexported_sessions() -> list[dict]:
    """All sessions where skipped=0 AND exported=0. Used for recovery scans;
    under EventBridge Scheduler the schedules themselves are durable, so this
    is rarely needed at runtime."""
    resp = _sessions.query(
        IndexName="by_date",
        KeyConditionExpression=Key("gsi1_pk").eq(_SESSION_GSI_PK),
        FilterExpression=Attr("skipped").eq(0) & Attr("exported").eq(0),
    )
    return [_to_dict(i) for i in resp.get("Items", [])]


def get_upcoming_session(within_days: int = 7) -> dict | None:
    """Any session (skipped or not) whose session_date is in [today, today+N].
    Includes skipped because an admin /skipsession is an explicit 'no session
    this week' — the cron must honor it and not create a replacement."""
    today = datetime.now(ZoneInfo(config.TIMEZONE)).date()
    end = today + timedelta(days=within_days)
    resp = _sessions.query(
        IndexName="by_date",
        KeyConditionExpression=(
            Key("gsi1_pk").eq(_SESSION_GSI_PK)
            & Key("session_date").between(today.isoformat(), end.isoformat())
        ),
        ScanIndexForward=True,
        Limit=1,
    )
    items = resp.get("Items", [])
    return _to_dict(items[0]) if items else None


# ── Responses ────────────────────────────────────────────────────────────────

def _response_sk(category: str, telegram_id: int) -> str:
    return f"{category}#{telegram_id}"


def toggle_response(
    session_id: str,
    telegram_id: int,
    username: str | None,
    first_name: str,
    category: str,
    responded_at: str,
) -> bool:
    """Returns True if added, False if removed."""
    sk = _response_sk(category, telegram_id)
    existing = _responses.get_item(
        Key={"session_id": session_id, "sk": sk},
        ConsistentRead=True,
    ).get("Item")
    if existing:
        _responses.delete_item(Key={"session_id": session_id, "sk": sk})
        return False
    item = {
        "session_id": session_id,
        "sk": sk,
        "telegram_id": telegram_id,
        "username": username,
        "first_name": first_name,
        "category": category,
        "responded_at": responded_at,
    }
    item = {k: v for k, v in item.items() if v is not None}
    _responses.put_item(Item=item)
    return True


def get_responses(session_id: str) -> list[dict]:
    resp = _responses.query(KeyConditionExpression=Key("session_id").eq(session_id))
    items = [_to_dict(i) for i in resp.get("Items", [])]
    return sorted(items, key=lambda r: r.get("responded_at", ""))


def get_responses_by_category(session_id: str, category: str) -> list[dict]:
    resp = _responses.query(
        KeyConditionExpression=(
            Key("session_id").eq(session_id) & Key("sk").begins_with(f"{category}#")
        ),
    )
    items = [_to_dict(i) for i in resp.get("Items", [])]
    return sorted(items, key=lambda r: r.get("responded_at", ""))
