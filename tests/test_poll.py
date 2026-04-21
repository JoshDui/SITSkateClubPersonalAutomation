"""Tests for poll message rendering (pure function, no AWS needed)."""
from __future__ import annotations


def test_build_poll_text_empty(ddb_tables):
    from shared import poll

    session = {
        "session_date": "2026-04-28",
        "start_time": "18:30",
        "end_time": "21:30",
        "location": "SIT @ Punggol Coast",
    }
    text = poll.build_poll_text(session, [])

    assert "SIT Inline Skate training session" in text
    assert "Tuesday, 28 April 2026" in text
    assert "6:30 pm - 9:30 pm" in text
    assert "SIT @ Punggol Coast" in text
    assert "0 people responded" in text


def test_build_poll_text_counts_unique_respondents(ddb_tables):
    from shared import poll

    session = {
        "session_date": "2026-04-28",
        "start_time": "18:30",
        "end_time": "21:30",
        "location": "SIT @ Punggol Coast",
    }
    responses = [
        # Same user, two categories → still 1 unique respondent
        {"telegram_id": 1, "first_name": "Alice", "category": "sit_student"},
        {"telegram_id": 1, "first_name": "Alice", "category": "rental_skates"},
        {"telegram_id": 2, "first_name": "Bob", "category": "non_sit"},
    ]
    text = poll.build_poll_text(session, responses)
    assert "2 people responded" in text
    assert "Alice" in text
    assert "Bob" in text


def test_build_keyboard_callback_data_format(ddb_tables):
    from shared import poll

    kb = poll.build_keyboard("01JABC1234567890ABCDEFGHJK")
    buttons = [row[0] for row in kb["inline_keyboard"]]
    callback_data = [b["callback_data"] for b in buttons]
    assert callback_data == [
        "vote:01JABC1234567890ABCDEFGHJK:sit_student",
        "vote:01JABC1234567890ABCDEFGHJK:non_sit",
        "vote:01JABC1234567890ABCDEFGHJK:rental_skates",
    ]
    # Telegram's 64-byte callback_data limit
    for cd in callback_data:
        assert len(cd.encode()) <= 64
