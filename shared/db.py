"""
DynamoDB access layer for the skate bot.

Tables (defined in infra/terraform/dynamodb.tf):

  members   — PK=username
  sessions  — PK=id (ULID)
  responses — PK=session_id, SK='{category}#{telegram_id}' (legacy vote rows)
  state     — PK=pk, SK=sk: booking documents, Telegram profiles and
              registry claims (see shared/booking_store.py)

Ported from the Azure Cosmos DB Table layer (azure/functionapp/shared/db.py).
Public function signatures and semantics are unchanged so the platform-neutral
modules (booking_flow, session_admin, ...) run as-is.

Session listings use a strongly consistent paginated Scan rather than a
secondary index: indexes are eventually consistent, and a session created
moments ago must be visible to the scheduler's "already exists" check and to
admin commands. At ~52 sessions/year the scan costs a few read units, and no
index keeps write capacity inside the provisioned free tier.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import boto3
from boto3.dynamodb.conditions import Key

from shared import config

_ddb = boto3.resource("dynamodb")
_members = _ddb.Table(config.TABLE_MEMBERS)
_sessions = _ddb.Table(config.TABLE_SESSIONS)
_responses = _ddb.Table(config.TABLE_RESPONSES)
_state = _ddb.Table(config.TABLE_STATE)

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


def _scan_all(table, **kwargs) -> list[dict]:
    items: list[dict] = []
    while True:
        resp = table.scan(**kwargs)
        items.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            return items
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]


def _query_all(table, **kwargs) -> list[dict]:
    items: list[dict] = []
    while True:
        resp = table.query(**kwargs)
        items.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            return items
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]


def _set_fields(table, key: dict, fields: dict, condition: str | None = None) -> None:
    """MERGE semantics: set only the given attributes, creating the item if
    missing (unless `condition` says otherwise). Matches the Azure layer's
    upsert_entity(mode=MERGE)."""
    names = {f"#f{i}": k for i, k in enumerate(fields)}
    values = {f":v{i}": v for i, v in enumerate(fields.values())}
    params = {
        "Key": key,
        "UpdateExpression": "SET " + ", ".join(f"#f{i} = :v{i}" for i in range(len(fields))),
        "ExpressionAttributeNames": names,
        "ExpressionAttributeValues": values,
    }
    if condition:
        params["ConditionExpression"] = condition
    table.update_item(**params)


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
    new_attrs = {
        "full_name": full_name,
        "is_sit_student": 1 if is_sit_student else 0,
        "student_id": student_id,
        "full_course_name": full_course_name,
        "cluster": cluster,
        "year": year,
        "imported_at": imported_at,
    }
    new_attrs = {k: v for k, v in new_attrs.items() if v is not None}

    existing = get_member(username)
    if existing is None:
        _members.put_item(Item={"username": username, **new_attrs})
        return "new"

    changed = any(
        existing.get(k) != new_attrs.get(k)
        for k in new_attrs if k != "imported_at"
    )
    if changed:
        # MERGE (not REPLACE) preserves fields written outside this path,
        # e.g. the rules-registry import's acknowledgement fields.
        update_member_fields(username, new_attrs)
        return "updated"
    return "unchanged"


def update_member_fields(username: str, fields: dict) -> None:
    """Merge `fields` into the member record, creating it if missing."""
    username = username.lstrip("@").lower()
    # The key attribute cannot appear in an UpdateExpression; update_item sets it.
    fields = {k: v for k, v in fields.items() if k != "username"}
    _set_fields(_members, {"username": username}, fields)


def get_member(username: str) -> dict | None:
    username = username.lstrip("@").lower()
    item = _members.get_item(Key={"username": username}, ConsistentRead=True).get("Item")
    return _to_dict(item)


def count_members() -> int:
    total = 0
    kwargs: dict = {"Select": "COUNT"}
    while True:
        resp = _members.scan(**kwargs)
        total += resp["Count"]
        if "LastEvaluatedKey" not in resp:
            return total
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]


# ── Sessions ─────────────────────────────────────────────────────────────────

def create_session(
    session_date: str,
    start_time: str,
    end_time: str,
    location: str,
    created_at: str | None = None,
    *, group_key: str = 'sit', group_name: str = 'SIT Inline Skate',
    poll_chat_id: int | None = None, poll_topic_id: int | None = None,
) -> str:
    """Returns the new session's id (ULID)."""
    session_id = _new_ulid()
    item = {
        "id": session_id,
        "session_date": session_date,
        "start_time": start_time,
        "end_time": end_time,
        "location": location,
        "skipped": 0,
        "exported": 0,
        "created_at": created_at or _now_iso_utc(),
        "group_key": group_key,
        "group_name": group_name,
    }
    if poll_chat_id is not None:
        item['poll_chat_id'] = poll_chat_id
        item['poll_topic_id'] = poll_topic_id or 0
    _sessions.put_item(Item=item, ConditionExpression="attribute_not_exists(id)")
    return session_id


def get_session(session_id: str, *, include_deleted: bool = False) -> dict | None:
    item = _sessions.get_item(Key={"id": session_id}, ConsistentRead=True).get("Item")
    if item is None or (item.get('deleted') and not include_deleted):
        return None
    return _to_dict(item)


def _visible_sessions() -> list[dict]:
    return [_to_dict(i) for i in _scan_all(_sessions, ConsistentRead=True) if not i.get('deleted')]


def list_sessions_for_admin() -> list[dict]:
    """Visible sessions, newest date/creation first; retained tombstones excluded."""
    return sorted(_visible_sessions(), key=lambda e: (e.get('session_date', ''),
                  e.get('created_at', ''), e.get('id', '')), reverse=True)


def mark_session_deleted(session_id: str, admin_id: int) -> None:
    """Recoverable removal. Sets flags only; never creates a row."""
    session = get_session(session_id, include_deleted=True)
    if session is None:
        raise ValueError('Session not found.')
    if session.get('deleted'):
        return
    _set_fields(_sessions, {"id": session_id},
                {'deleted': True, 'deleted_at': _now_iso_utc(), 'deleted_by': str(admin_id)},
                condition="attribute_exists(id)")


def get_latest_session(group_key: str = 'sit') -> dict | None:
    """Most recent visible session for the group, by session_date."""
    items = [s for s in list_sessions_for_admin() if s.get('group_key', 'sit') == group_key]
    if not items:
        return None
    return items[0]


def set_poll_message_id(session_id: str, message_id: int, chat_id: int | None = None) -> None:
    _set_fields(_sessions, {"id": session_id}, {
        "poll_message_id": message_id,
        "poll_chat_id": chat_id if chat_id is not None else config.attendance_poll_chat_id(),
    })


def mark_session_skipped(session_id: str) -> None:
    _set_fields(_sessions, {"id": session_id}, {"skipped": 1})


def mark_session_exported(session_id: str) -> None:
    _set_fields(_sessions, {"id": session_id}, {"exported": 1})


def update_session_details(
    session_id: str,
    session_date: str,
    start_time: str,
    end_time: str,
    location: str,
) -> None:
    _set_fields(_sessions, {"id": session_id}, {
        "session_date": session_date,
        "start_time": start_time,
        "end_time": end_time,
        "location": location,
    })


def list_unexported_sessions() -> list[dict]:
    """All visible sessions where skipped=0 AND exported=0."""
    return [s for s in _visible_sessions() if not s.get('skipped') and not s.get('exported')]


def list_sessions_since(since_date: str, include_skipped: bool = False) -> list[dict]:
    """All visible sessions with session_date >= since_date (ISO YYYY-MM-DD),
    sorted ascending."""
    datetime.strptime(since_date, "%Y-%m-%d")  # reject anything but a plain date
    items = [s for s in _visible_sessions() if s.get('session_date', '') >= since_date
             and (include_skipped or not s.get('skipped'))]
    return sorted(items, key=lambda r: r.get("session_date", ""))


def get_upcoming_session(within_days: int = 7, group_key: str = 'sit') -> dict | None:
    """Any session (skipped or not) for the group whose session_date is in
    [today, today+N]. Includes skipped because an admin /skipsession is an
    explicit 'no session this week' — the cron must honor it."""
    today = datetime.now(ZoneInfo(config.TIMEZONE)).date()
    start, end = today.isoformat(), (today + timedelta(days=within_days)).isoformat()
    items = [s for s in _visible_sessions()
             if start <= s.get('session_date', '') <= end and s.get('group_key', 'sit') == group_key]
    if not items:
        return None
    return min(items, key=lambda e: e.get("session_date", ""))


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
    """Returns True if added, False if removed. Only used when bookings are
    disabled; the booking document (shared/booking_store.py) replaces it."""
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


def get_legacy_responses(session_id: str) -> list[dict]:
    items = [_to_dict(i) for i in _query_all(
        _responses, KeyConditionExpression=Key("session_id").eq(session_id), ConsistentRead=True)]
    return sorted(items, key=lambda r: r.get("responded_at", ""))


def get_responses(session_id: str) -> list[dict]:
    from shared import booking_store, rentals
    state = booking_store.read(session_id)
    return rentals.responses(state, session_id) if state is not None else get_legacy_responses(session_id)


def get_responses_by_category(session_id: str, category: str) -> list[dict]:
    return [r for r in get_responses(session_id) if r.get('category') == category]
