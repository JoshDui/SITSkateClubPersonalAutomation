"""
Poll message rendering + inline keyboard.

The output of `build_keyboard()` is a plain dict matching Telegram's
InlineKeyboardMarkup JSON — no telegram.Bot or PTB types leak into this
module. It's a pure function over `session` + `responses` dicts.
"""
from __future__ import annotations

from datetime import datetime

from shared import config

CATEGORY_KEYS = ("sit_student", "non_sit", "rental_skates")

_STATIC_LABELS = {
    "sit_student": "I am a SIT student and am able to make it",
    "non_sit": "I am not a SIT student and am able to make it (have filled up indemnity form)",
}


def category_labels() -> dict[str, str]:
    """Full category labels including the dynamic rental_skates one that
    embeds the admin handle from SSM."""
    return {
        "sit_student": _STATIC_LABELS["sit_student"],
        "non_sit": _STATIC_LABELS["non_sit"],
        "rental_skates": (
            f"I need rental skates! "
            f"(Please dm @{config.rental_skates_handle()} to let us know your skate size in EU) "
            f"Also inform us if you need guards!"
        ),
    }


def _format_date(session_date: str) -> str:
    """'2026-04-21' → 'Tuesday, 21 April 2026'."""
    d = datetime.strptime(session_date, "%Y-%m-%d")
    return d.strftime("%A, %d %B %Y").replace(" 0", " ", 1)


def _format_time(t: str) -> str:
    """'18:30' → '6:30 pm'."""
    h, m = map(int, t.split(":"))
    period = "am" if h < 12 else "pm"
    h12 = h % 12 or 12
    return f"{h12}:{m:02d} {period}"


def build_poll_text(session: dict, responses: list[dict]) -> str:
    date_str = _format_date(session["session_date"])
    start_str = _format_time(session["start_time"])
    end_str = _format_time(session["end_time"])

    labels = category_labels()
    by_cat: dict[str, list[str]] = {cat: [] for cat in CATEGORY_KEYS}
    for r in responses:
        cat = r.get("category")
        if cat in by_cat:
            by_cat[cat].append(r.get("first_name", ""))

    total = len({r["telegram_id"] for r in responses})

    lines = [
        "SIT Inline Skate training session",
        "",
        f"Date: {date_str}",
        f"Time: {start_str} - {end_str}",
        f"Meeting Location: {session['location']}",
        "",
    ]
    for cat_key in CATEGORY_KEYS:
        names = by_cat[cat_key]
        lines.append(f"{labels[cat_key]}: ({len(names)} \U0001f465)")
        lines.extend(names)
        lines.append("")
    lines.append(f"\U0001f465 {total} {'person' if total == 1 else 'people'} responded")
    return "\n".join(lines)


def build_keyboard(session_id: str) -> dict:
    """Telegram InlineKeyboardMarkup dict. Callback data format:
    'vote:<session_id>:<category>' — stays under the 64-byte limit even
    with a 26-char ULID session_id."""
    return {
        "inline_keyboard": [
            [{"text": _STATIC_LABELS["sit_student"], "callback_data": f"vote:{session_id}:sit_student"}],
            [{"text": _STATIC_LABELS["non_sit"], "callback_data": f"vote:{session_id}:non_sit"}],
            [{"text": "I need rental skates!", "callback_data": f"vote:{session_id}:rental_skates"}],
        ],
    }
