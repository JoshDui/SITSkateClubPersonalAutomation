"""Unit tests for the DynamoDB access layer."""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest


def test_upsert_and_get_member(db_module):
    db = db_module
    res = db.upsert_member(
        username="AsheEtt", full_name="Ashley Teo", is_sit_student=True,
        student_id="2302532", full_course_name="BBus Aviation", cluster="BCD",
        year="2", imported_at="2026-04-21T00:00:00+00:00",
    )
    assert res == "new"

    fetched = db.get_member("@AsheEtt")  # handle strip + lowercase
    assert fetched is not None
    assert fetched["username"] == "asheett"
    assert fetched["full_name"] == "Ashley Teo"
    assert fetched["is_sit_student"] == 1

    # Re-upsert identical → unchanged
    res2 = db.upsert_member(
        username="asheett", full_name="Ashley Teo", is_sit_student=True,
        student_id="2302532", full_course_name="BBus Aviation", cluster="BCD",
        year="2", imported_at="2026-04-22T00:00:00+00:00",
    )
    assert res2 == "unchanged"

    # Change a field → updated
    res3 = db.upsert_member(
        username="asheett", full_name="Ashley Teo Xuan Wen", is_sit_student=True,
        student_id="2302532", full_course_name="BBus Aviation", cluster="BCD",
        year="3", imported_at="2026-04-23T00:00:00+00:00",
    )
    assert res3 == "updated"


def test_count_members(db_module):
    db = db_module
    assert db.count_members() == 0
    for i, handle in enumerate(["a", "b", "c"]):
        db.upsert_member(
            username=handle, full_name=f"Name {i}", is_sit_student=True,
            student_id=None, full_course_name=None, cluster=None, year=None,
            imported_at="2026-04-21T00:00:00+00:00",
        )
    assert db.count_members() == 3


def test_create_and_get_session(db_module):
    db = db_module
    sid = db.create_session(
        session_date="2026-04-28",
        start_time="18:30",
        end_time="21:30",
        location="SIT @ Punggol Coast",
    )
    assert isinstance(sid, str) and len(sid) > 0
    s = db.get_session(sid)
    assert s is not None
    assert s["session_date"] == "2026-04-28"
    assert s["skipped"] == 0
    assert s["exported"] == 0


def test_get_latest_session_orders_by_date(db_module):
    db = db_module
    sid_old = db.create_session("2026-04-21", "18:30", "21:30", "A")
    sid_new = db.create_session("2026-05-05", "18:30", "21:30", "B")
    sid_mid = db.create_session("2026-04-28", "18:30", "21:30", "C")

    latest = db.get_latest_session()
    assert latest is not None
    assert latest["id"] == sid_new  # 2026-05-05 is latest
    # Unused vars to satisfy linters
    _ = sid_old, sid_mid


def test_mark_skipped_and_exported(db_module):
    db = db_module
    sid = db.create_session("2026-04-28", "18:30", "21:30", "loc")

    db.mark_session_skipped(sid)
    assert db.get_session(sid)["skipped"] == 1

    db.mark_session_exported(sid)
    assert db.get_session(sid)["exported"] == 1


def test_update_session_details(db_module):
    db = db_module
    sid = db.create_session("2026-04-28", "18:30", "21:30", "Old Place")
    db.update_session_details(sid, "2026-04-29", "19:00", "22:00", "New Place")
    s = db.get_session(sid)
    assert s["session_date"] == "2026-04-29"
    assert s["start_time"] == "19:00"
    assert s["end_time"] == "22:00"
    assert s["location"] == "New Place"


def test_get_upcoming_session_includes_skipped(db_module):
    """A skipped session for this week still counts — this is the point of
    the /skipsession admin action."""
    db = db_module
    tz = ZoneInfo("Asia/Singapore")
    in_3_days = (datetime.now(tz) + timedelta(days=3)).strftime("%Y-%m-%d")
    sid = db.create_session(in_3_days, "18:30", "21:30", "loc")
    db.mark_session_skipped(sid)

    up = db.get_upcoming_session(within_days=7)
    assert up is not None
    assert up["id"] == sid
    assert up["skipped"] == 1


def test_get_upcoming_session_ignores_past(db_module):
    db = db_module
    tz = ZoneInfo("Asia/Singapore")
    yesterday = (datetime.now(tz) - timedelta(days=1)).strftime("%Y-%m-%d")
    db.create_session(yesterday, "18:30", "21:30", "loc")

    up = db.get_upcoming_session(within_days=7)
    assert up is None


def test_list_unexported_sessions(db_module):
    db = db_module
    s1 = db.create_session("2026-04-21", "18:30", "21:30", "a")
    s2 = db.create_session("2026-04-28", "18:30", "21:30", "b")
    s3 = db.create_session("2026-05-05", "18:30", "21:30", "c")
    db.mark_session_skipped(s1)
    db.mark_session_exported(s3)

    pending = {row["id"] for row in db.list_unexported_sessions()}
    assert pending == {s2}


def test_toggle_response_add_then_remove(db_module):
    db = db_module
    sid = db.create_session("2026-04-28", "18:30", "21:30", "loc")

    added = db.toggle_response(
        session_id=sid, telegram_id=42, username="asheett", first_name="Ashley",
        category="sit_student", responded_at="2026-04-21T12:00:00+00:00",
    )
    assert added is True

    removed = db.toggle_response(
        session_id=sid, telegram_id=42, username="asheett", first_name="Ashley",
        category="sit_student", responded_at="2026-04-21T12:01:00+00:00",
    )
    assert removed is False

    assert db.get_responses(sid) == []


def test_get_responses_by_category_begins_with(db_module):
    db = db_module
    sid = db.create_session("2026-04-28", "18:30", "21:30", "loc")
    db.toggle_response(
        session_id=sid, telegram_id=1, username="a", first_name="Alice",
        category="sit_student", responded_at="2026-04-21T12:00:00+00:00",
    )
    db.toggle_response(
        session_id=sid, telegram_id=2, username="b", first_name="Bob",
        category="non_sit", responded_at="2026-04-21T12:01:00+00:00",
    )
    db.toggle_response(
        session_id=sid, telegram_id=1, username="a", first_name="Alice",
        category="rental_skates", responded_at="2026-04-21T12:02:00+00:00",
    )

    sit = db.get_responses_by_category(sid, "sit_student")
    assert [r["first_name"] for r in sit] == ["Alice"]

    rental = db.get_responses_by_category(sid, "rental_skates")
    assert [r["first_name"] for r in rental] == ["Alice"]

    all_for_session = db.get_responses(sid)
    assert len(all_for_session) == 3
