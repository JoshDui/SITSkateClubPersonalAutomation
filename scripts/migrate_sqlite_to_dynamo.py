"""
One-shot migration of Phase 3 SQLite data → DynamoDB tables.

Idempotent by design — members are upserted by username, sessions by id,
responses by (session_id, sk). Re-running does no harm.

Usage:
    python scripts/migrate_sqlite_to_dynamo.py \
        --source C:/UniPain/skatetelegrambot/skatebot.db \
        --env prod

Session IDs: Phase 3 used integer autoincrement. We preserve those as strings
(e.g., "1", "2", ...) so the responses table's foreign-key-like session_id
stays consistent. New sessions post-migration use ULIDs — the string space
doesn't collide.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

import boto3

_SESSION_GSI_PK = "SESSION"


def main() -> int:
    ap = argparse.ArgumentParser(description="Migrate Phase 3 SQLite → DynamoDB.")
    ap.add_argument("--source", required=True, help="Path to Phase 3 skatebot.db")
    ap.add_argument("--env", default="prod", help="Target env (prod/dev); default prod")
    ap.add_argument("--dry-run", action="store_true", help="Read but don't write")
    args = ap.parse_args()

    src = Path(args.source)
    if not src.exists():
        print(f"Source DB not found: {src}", file=sys.stderr)
        return 1

    prefix = f"skatebot-{args.env}"
    ddb = boto3.resource("dynamodb")
    t_members = ddb.Table(f"{prefix}-members")
    t_sessions = ddb.Table(f"{prefix}-sessions")
    t_responses = ddb.Table(f"{prefix}-responses")

    con = sqlite3.connect(str(src))
    con.row_factory = sqlite3.Row

    m_count = _migrate_members(con, t_members, args.dry_run)
    s_count = _migrate_sessions(con, t_sessions, args.dry_run)
    r_count = _migrate_responses(con, t_responses, args.dry_run)
    con.close()

    print(f"Migration {'(dry run) ' if args.dry_run else ''}complete:")
    print(f"  members:   {m_count}")
    print(f"  sessions:  {s_count}")
    print(f"  responses: {r_count}")
    return 0


def _migrate_members(con: sqlite3.Connection, table, dry: bool) -> int:
    rows = con.execute("SELECT * FROM members").fetchall()
    with table.batch_writer() if not dry else _NoOpBatchWriter() as bw:
        for r in rows:
            item = {
                "username": r["username"],
                "full_name": r["full_name"],
                "is_sit_student": int(r["is_sit_student"] or 0),
                "student_id": r["student_id"],
                "full_course_name": r["full_course_name"],
                "cluster": r["cluster"],
                "year": r["year"],
                "imported_at": r["imported_at"],
            }
            bw.put_item(Item=_compact(item))
    return len(rows)


def _migrate_sessions(con: sqlite3.Connection, table, dry: bool) -> int:
    rows = con.execute("SELECT * FROM sessions").fetchall()
    with table.batch_writer() if not dry else _NoOpBatchWriter() as bw:
        for r in rows:
            item = {
                "id": str(r["id"]),
                "gsi1_pk": _SESSION_GSI_PK,
                "session_date": r["session_date"],
                "start_time": r["start_time"],
                "end_time": r["end_time"],
                "location": r["location"],
                "poll_message_id": r["poll_message_id"],
                "skipped": int(r["skipped"] or 0),
                "exported": int(r["exported"] or 0),
                "created_at": r["created_at"],
            }
            bw.put_item(Item=_compact(item))
    return len(rows)


def _migrate_responses(con: sqlite3.Connection, table, dry: bool) -> int:
    rows = con.execute("SELECT * FROM responses").fetchall()
    with table.batch_writer() if not dry else _NoOpBatchWriter() as bw:
        for r in rows:
            item = {
                "session_id": str(r["session_id"]),
                "sk": f"{r['category']}#{r['telegram_id']}",
                "telegram_id": int(r["telegram_id"]),
                "username": r["username"],
                "first_name": r["first_name"],
                "category": r["category"],
                "responded_at": r["responded_at"],
            }
            bw.put_item(Item=_compact(item))
    return len(rows)


def _compact(item: dict) -> dict:
    return {k: v for k, v in item.items() if v is not None}


class _NoOpBatchWriter:
    """Stand-in for the DynamoDB batch_writer context manager in --dry-run mode."""
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False
    def put_item(self, Item):  # noqa: N803 — match boto3 kwarg name
        print(f"[dry-run] put_item: {Item}")


if __name__ == "__main__":
    # Default AWS region if not set
    os.environ.setdefault("AWS_DEFAULT_REGION", "ap-southeast-1")
    sys.exit(main())
