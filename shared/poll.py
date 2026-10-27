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
        "rental_skates": "Rental skates (current SIT students only)" if config.BOOKINGS_ENABLED else (
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


def _name_lines(names: list[str], budget: int = 700) -> list[str]:
    """Keep counts accurate while bounding names below Telegram's 4096
    character limit, including worst-case UTF-16 emoji names."""
    shown = []
    for name in names:
        cost = len(name.encode('utf-16-le')) // 2 + 1
        if cost > budget:
            break
        shown.append(name)
        budget -= cost
    if len(shown) < len(names):
        shown.append(f'... and {len(names) - len(shown)} more')
    return shown


def _names_by_category(responses: list[dict]) -> dict[str, list[str]]:
    by_cat: dict[str, list[str]] = {cat: [] for cat in CATEGORY_KEYS}
    for r in responses:
        cat = r.get("category")
        if cat in by_cat:
            by_cat[cat].append(str(r.get("first_name") or r.get('telegram_id', ''))[:80].replace('\n', ' '))
    return by_cat


def _respondent_line(responses: list[dict]) -> str:
    total = len({r["telegram_id"] for r in responses})
    return f"\U0001f465 {total} {'person' if total == 1 else 'people'} responded"


def build_event_text(session: dict, responses: list[dict]) -> str:
    """Special-event announcement. Shows Telegram display names only; the
    registration details (student numbers, partial NRIC/FIN) stay private."""
    by_cat = _names_by_category(responses)
    lines = [
        f"\U0001f6fc {session.get('title') or 'Special event'}",
        "",
        f"Date: {_format_date(session['session_date'])}",
        f"Time: {_format_time(session['start_time'])} - {_format_time(session['end_time'])}",
        f"Location: {session['location']}",
    ]
    if session.get('details'):
        lines += ["", session['details']]
    lines += ["", "Register with the buttons below (opens a private chat with the bot).", ""]
    for cat_key, label in (('sit_student', 'SIT students'), ('non_sit', 'Alumni / External'),
                           ('rental_skates', 'Rental skates')):
        names = by_cat[cat_key]
        lines.append(f"{label}: ({len(names)} \U0001f465)")
        lines.extend(_name_lines(names, 600))
        lines.append("")
    lines.append(_respondent_line(responses))
    return "\n".join(lines)


def build_poll_text(session: dict, responses: list[dict]) -> str:
    if session.get('kind') == 'event':
        return build_event_text(session, responses)
    date_str = _format_date(session["session_date"])
    start_str = _format_time(session["start_time"])
    end_str = _format_time(session["end_time"])

    labels = category_labels()
    by_cat = _names_by_category(responses)

    lines = [
        f"{session.get('group_name', 'SIT Inline Skate')} training session",
        "",
        f"Date: {date_str}",
        f"Time: {start_str} - {end_str}",
        f"Meeting Location: {session['location']}",
        "Attendance and student status are self-declared; registry verification is not required.",
        "",
    ]
    for cat_key in CATEGORY_KEYS:
        names = by_cat[cat_key]
        lines.append(f"{labels[cat_key]}: ({len(names)} \U0001f465)")
        lines.extend(_name_lines(names))
        lines.append("")
    lines.append(_respondent_line(responses))
    return "\n".join(lines)


def build_keyboard(session_id: str, session: dict | None = None) -> dict:
    """Telegram InlineKeyboardMarkup dict. Callback data format:
    'vote:<session_id>:<category>' — stays under the 64-byte limit even
    with a 26-char ULID session_id. Special events register through deep
    links (start=evs_/eva_<session_id>, 30 chars; the limit is 64)."""
    if session and session.get('kind') == 'event':
        link = f"https://t.me/{config.BOT_USERNAME}?start="
        return {"inline_keyboard": [
            [{"text": "Register: SIT student", "url": f"{link}evs_{session_id}"}],
            [{"text": "Register: Alumni / External", "url": f"{link}eva_{session_id}"}],
        ]}
    return {
        "inline_keyboard": [
            [{"text": _STATIC_LABELS["sit_student"], "callback_data": f"vote:{session_id}:sit_student"}],
            [{"text": _STATIC_LABELS["non_sit"], "callback_data": f"vote:{session_id}:non_sit"}],
            [({"text": "I need rental skates!", "url": f"https://t.me/{config.BOT_USERNAME}?start=rent_{session_id}"}
              if config.BOOKINGS_ENABLED and config.BOT_USERNAME else
              {"text": "I need rental skates!", "callback_data": f"vote:{session_id}:rental_skates"})],
        ],
    }
