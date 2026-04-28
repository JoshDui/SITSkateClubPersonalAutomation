"""
Cosmos DB Table API access layer for the skate bot.

Tables (defined in infra/terraform/cosmos.tf):

  members   — PartitionKey='MEMBER', RowKey=username
  sessions  — PartitionKey='SESSION', RowKey=id (ULID)
  responses — PartitionKey=session_id, RowKey='{category}#{telegram_id}'

Public function signatures mirror the AWS shared/db.py exactly so handler
code can be ported without changing call sites. Return types are plain
dicts (Cosmos returns native Python types — no Decimal unwrapping needed).

Why single-partition tables for members and sessions: at 107 members and
~52 sessions/year, a single partition fits well within Cosmos's 20 GB
logical partition limit and lets us use the partition key as a sentinel
for `query_entities`. If this ever grew to thousands of members, we'd
shard members on the first character of the username.

Why upsert_entity (not update_entity) for partial updates: DynamoDB's
update_item is upsert-by-default; Cosmos's update_entity raises 404 if
the entity doesn't exist. upsert_entity with UpdateMode.MERGE matches
the AWS semantic of "set these attrs, create the entity if missing".
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from azure.data.tables import TableServiceClient, UpdateMode
from azure.core.exceptions import ResourceNotFoundError
from azure.identity import DefaultAzureCredential

from shared import config

_credential = DefaultAzureCredential()
_table_service = TableServiceClient(
    endpoint=config.COSMOS_ENDPOINT,
    credential=_credential,
)
_members = _table_service.get_table_client(config.TABLE_MEMBERS)
_sessions = _table_service.get_table_client(config.TABLE_SESSIONS)
_responses = _table_service.get_table_client(config.TABLE_RESPONSES)

_MEMBER_PK = "MEMBER"
_SESSION_PK = "SESSION"


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


def _entity_to_dict(entity) -> dict:
    """Strip Cosmos system fields (etag, Timestamp, metadata) and rename
    PartitionKey/RowKey back to the AWS-native attribute names so callers
    see the same shape as before.

    For session entities, derive ``id`` from RowKey: Cosmos DB Table API
    rejects ``id`` as a reserved property name (it is the underlying
    document key), so we cannot store it directly — we re-attach it on
    read to preserve the AWS-compatible ``session["id"]`` accessor."""
    d = dict(entity)
    if d.get("PartitionKey") == _SESSION_PK:
        d["id"] = d.get("RowKey")
    d.pop("PartitionKey", None)
    d.pop("RowKey", None)
    d.pop("etag", None)
    d.pop("Timestamp", None)
    d.pop("odata.metadata", None)
    return d


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
    entity = {"PartitionKey": _MEMBER_PK, "RowKey": username, "username": username, **new_attrs}

    try:
        existing = _members.get_entity(partition_key=_MEMBER_PK, row_key=username)
    except ResourceNotFoundError:
        _members.create_entity(entity=entity)
        return "new"

    existing_dict = _entity_to_dict(existing)
    changed = any(
        existing_dict.get(k) != new_attrs.get(k)
        for k in new_attrs if k != "imported_at"
    )
    if changed:
        # MERGE (not REPLACE) preserves any fields written outside the import
        # path (e.g. a future /sethandle command). The file's docstring at the
        # top promises MERGE semantics throughout — this honours it.
        _members.upsert_entity(entity=entity, mode=UpdateMode.MERGE)
        return "updated"
    return "unchanged"


def get_member(username: str) -> dict | None:
    username = username.lstrip("@").lower()
    try:
        entity = _members.get_entity(partition_key=_MEMBER_PK, row_key=username)
    except ResourceNotFoundError:
        return None
    return _entity_to_dict(entity)


def count_members() -> int:
    """Cosmos Table API has no count primitive; query the partition with a
    minimal projection. Cheap at our scale (~107 rows)."""
    return sum(
        1 for _ in _members.query_entities(
            f"PartitionKey eq '{_MEMBER_PK}'",
            select=["RowKey"],
        )
    )


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
    entity = {
        "PartitionKey": _SESSION_PK,
        "RowKey": session_id,
        "session_date": session_date,
        "start_time": start_time,
        "end_time": end_time,
        "location": location,
        "skipped": 0,
        "exported": 0,
        "created_at": created_at or _now_iso_utc(),
    }
    _sessions.create_entity(entity=entity)
    return session_id


def get_session(session_id: str) -> dict | None:
    try:
        entity = _sessions.get_entity(partition_key=_SESSION_PK, row_key=session_id)
    except ResourceNotFoundError:
        return None
    return _entity_to_dict(entity)


def get_latest_session() -> dict | None:
    """Most recent session by session_date. Implementation differs from AWS:
    Cosmos Table API has no native sorting on non-key properties, so we scan
    the SESSION partition and pick the max client-side. Fine at our scale
    (~52 sessions/year)."""
    items = list(_sessions.query_entities(f"PartitionKey eq '{_SESSION_PK}'"))
    if not items:
        return None
    latest = max(items, key=lambda e: e.get("session_date", ""))
    return _entity_to_dict(latest)


def set_poll_message_id(session_id: str, message_id: int) -> None:
    _sessions.upsert_entity(
        entity={
            "PartitionKey": _SESSION_PK,
            "RowKey": session_id,
            "poll_message_id": message_id,
        },
        mode=UpdateMode.MERGE,
    )


def mark_session_skipped(session_id: str) -> None:
    _sessions.upsert_entity(
        entity={"PartitionKey": _SESSION_PK, "RowKey": session_id, "skipped": 1},
        mode=UpdateMode.MERGE,
    )


def mark_session_exported(session_id: str) -> None:
    _sessions.upsert_entity(
        entity={"PartitionKey": _SESSION_PK, "RowKey": session_id, "exported": 1},
        mode=UpdateMode.MERGE,
    )


def update_session_details(
    session_id: str,
    session_date: str,
    start_time: str,
    end_time: str,
    location: str,
) -> None:
    _sessions.upsert_entity(
        entity={
            "PartitionKey": _SESSION_PK,
            "RowKey": session_id,
            "session_date": session_date,
            "start_time": start_time,
            "end_time": end_time,
            "location": location,
        },
        mode=UpdateMode.MERGE,
    )


def list_unexported_sessions() -> list[dict]:
    """All sessions where skipped=0 AND exported=0."""
    filt = (
        f"PartitionKey eq '{_SESSION_PK}' and skipped eq 0 and exported eq 0"
    )
    return [_entity_to_dict(e) for e in _sessions.query_entities(filt)]


def get_upcoming_session(within_days: int = 7) -> dict | None:
    """Any session (skipped or not) whose session_date is in [today, today+N].
    Includes skipped because an admin /skipsession is an explicit 'no session
    this week' — the cron must honor it."""
    today = datetime.now(ZoneInfo(config.TIMEZONE)).date()
    end = today + timedelta(days=within_days)
    filt = (
        f"PartitionKey eq '{_SESSION_PK}' "
        f"and session_date ge '{today.isoformat()}' "
        f"and session_date le '{end.isoformat()}'"
    )
    items = list(_sessions.query_entities(filt))
    if not items:
        return None
    earliest = min(items, key=lambda e: e.get("session_date", ""))
    return _entity_to_dict(earliest)


# ── Responses ────────────────────────────────────────────────────────────────

_RESPONSE_SEP = ":"  # Cosmos Table API forbids '#' in row keys (along with '/', '\\', '?'
                     # and control chars), so we cannot reuse the AWS DynamoDB '{category}#{id}'
                     # separator. ':' is allowed, has no OData meaning, and the next codepoint
                     # ';' is plain printable ASCII — clean for the prefix-range query in
                     # get_responses_by_category.


def _response_rk(category: str, telegram_id: int) -> str:
    return f"{category}{_RESPONSE_SEP}{telegram_id}"


def toggle_response(
    session_id: str,
    telegram_id: int,
    username: str | None,
    first_name: str,
    category: str,
    responded_at: str,
) -> bool:
    """Returns True if added, False if removed.

    Race-condition note: identical to AWS — two concurrent taps from the
    same user can both observe absence and both add. Acceptable at our
    scale; same caveat as the AWS implementation."""
    rk = _response_rk(category, telegram_id)
    try:
        _responses.get_entity(partition_key=session_id, row_key=rk)
        _responses.delete_entity(partition_key=session_id, row_key=rk)
        return False
    except ResourceNotFoundError:
        pass

    entity = {
        "PartitionKey": session_id,
        "RowKey": rk,
        "session_id": session_id,
        "telegram_id": telegram_id,
        "username": username,
        "first_name": first_name,
        "category": category,
        "responded_at": responded_at,
    }
    entity = {k: v for k, v in entity.items() if v is not None}
    _responses.create_entity(entity=entity)
    return True


def get_responses(session_id: str) -> list[dict]:
    items = [
        _entity_to_dict(e)
        for e in _responses.query_entities(f"PartitionKey eq '{session_id}'")
    ]
    return sorted(items, key=lambda r: r.get("responded_at", ""))


def get_responses_by_category(session_id: str, category: str) -> list[dict]:
    """RowKey begins with '{category}:'. The OData equivalent of begins_with
    is a lex-range; the next codepoint after ':' (0x3A) is ';' (0x3B)."""
    filt = (
        f"PartitionKey eq '{session_id}' "
        f"and RowKey ge '{category}:' "
        f"and RowKey lt '{category};'"
    )
    items = [_entity_to_dict(e) for e in _responses.query_entities(filt)]
    return sorted(items, key=lambda r: r.get("responded_at", ""))
