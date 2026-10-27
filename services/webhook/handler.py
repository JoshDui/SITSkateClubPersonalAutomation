"""
Telegram webhook Lambda.

Invoked via the Lambda Function URL on every Telegram update (message or
callback_query). Authenticated by the secret token Telegram includes in the
`X-Telegram-Bot-Api-Secret-Token` header.

Responds 200 OK quickly — any user-visible work runs inline. Telegram
retries if we take >60s or return a non-2xx.

Ported from the Azure Function (azure/functionapp/webhook/__init__.py). Only
the platform edges differ: the Function URL event/response shape, and export
jobs are EventBridge Scheduler one-offs (shared/export_schedule.py), so
/setsession reschedules the existing job instead of queueing a second one.
"""
from __future__ import annotations

import base64
import hmac
import json
import logging
import os
import re
import tempfile
from contextlib import suppress
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from shared import config, db, export_schedule, importer, poll, telegram

log = logging.getLogger("webhook")
log.setLevel(logging.INFO)

# ULID alphabet (Crockford base32, exactly 26 chars). callback_query.data is
# user-controlled, so we validate session_id matches the ULID shape before any
# DB call.
_ULID_RE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")


def lambda_handler(event: dict, context) -> dict:
    try:
        if not _verify_secret(event):
            log.warning("Rejected webhook with invalid secret token.")
            return _resp(401, "unauthorized")

        raw = event.get("body") or "{}"
        if event.get("isBase64Encoded"):
            raw = base64.b64decode(raw)
        body = json.loads(raw)
        log.info("update_id=%s keys=%s", body.get("update_id"), list(body.keys()))

        if "callback_query" in body:
            _handle_callback(body["callback_query"], body.get("update_id", -1))
        elif "message" in body:
            _handle_message(body["message"], body.get("update_id", -1))

        return _resp(200, "ok")
    except Exception:
        log.exception("Webhook handler failed.")
        # Return 200 anyway — a 5xx makes Telegram retry, which usually makes
        # things worse. We've logged the failure; the CloudWatch alarm will fire.
        # Booking taps are the exception: their writes are idempotent per
        # update_id, so a retry is how a failed poll refresh gets repaired.
        is_booking = (locals().get('body', {}).get('callback_query', {}).get('data', '').startswith(('r:', 'vote:', 'e:'))
                      and getattr(config, 'BOOKINGS_ENABLED', False))
        return _resp(503 if is_booking else 200, "error-logged")


# ── Auth + response helpers ─────────────────────────────────────────────────

def _verify_secret(event: dict) -> bool:
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    got = headers.get("x-telegram-bot-api-secret-token") or ""
    # Constant-time comparison: the Function URL is public.
    return hmac.compare_digest(got.encode(), config.webhook_secret().encode())


def _resp(code: int, body: str) -> dict:
    return {"statusCode": code, "body": body, "headers": {"Content-Type": "text/plain"}}


# ── Message dispatch (commands) ─────────────────────────────────────────────

def _handle_message(msg: dict, update_id: int = -1) -> None:
    text = (msg.get("text") or msg.get("caption") or "").strip()
    user = msg.get("from") or {}
    chat = msg.get("chat") or {}
    user_id = user.get("id")
    own_dm = chat.get('type') == 'private' and chat.get('id') == user_id
    bookings = getattr(config, 'BOOKINGS_ENABLED', False)

    if not text.startswith("/"):
        # Answers to a special-event form (setup or registration) in the bot DM.
        if bookings and own_dm and type(user_id) is int and not user.get('is_bot'):
            from shared import events
            events.handle_text(msg, update_id)
        return

    cmd, _, args_str = text.partition(" ")
    cmd = cmd.split("@", 1)[0].lstrip("/").lower()
    args = args_str.split() if args_str else []

    if cmd == 'start' and bookings:
        if not own_dm:
            return
        from shared import booking_flow
        if args and args[0].startswith(('rent_', 'rules_')):
            prefix, sid = args[0].split('_', 1)
            booking_flow.start(user, sid, rules_only=prefix == 'rules')
        elif args and args[0].startswith(('evs_', 'eva_')):
            from shared import events
            prefix, sid = args[0].split('_', 1)
            events.start_registration(user, sid, 'sit' if prefix == 'evs' else 'alumni')
        else:
            telegram.send_message(chat['id'], 'Use the rental button on your training session poll to book skates.')
        return

    if cmd == 'cancel' and bookings:
        # Anyone can abandon their own form; it is not an admin command.
        if own_dm:
            from shared import events
            events.cancel_form(user)
        return

    # Authorization uses Telegram's authenticated sender ID, never a handle,
    # display name, forwarded origin, or group-admin status. Menus are not ACLs.
    if (type(user_id) is not int or user_id <= 0 or user.get('is_bot')
            or msg.get('sender_chat')):
        return

    if user_id not in config.admin_ids():
        telegram.send_message(chat["id"], "⛔ Admin only.")
        return

    handler = _COMMAND_HANDLERS.get(cmd)
    if handler is None:
        return
    # All admin output stays in the authorized account's own private chat.
    # /sendpoll still intentionally publishes the poll in the configured group.
    if chat.get('type') != 'private' or chat.get('id') != user_id:
        telegram.send_message(chat['id'], 'Please use admin commands in your private chat with the bot.')
        return
    handler(msg, args)


def _cmd_sendpoll(msg: dict, args: list[str]) -> None:
    from shared import destinations
    try:
        group_key = destinations.key(' '.join(args))
        chat_id, topic_id = destinations.route(group_key)
    except ValueError as exc:
        telegram.send_message(msg['chat']['id'], str(exc))
        return
    now = datetime.now(ZoneInfo(config.TIMEZONE))
    session_date = destinations.next_date(group_key, now)

    session_id = db.create_session(
        session_date=session_date,
        start_time=config.DEFAULT_SESSION_START,
        end_time=config.DEFAULT_SESSION_END,
        location=config.DEFAULT_SESSION_LOCATION,
        created_at=datetime.now(UTC).isoformat(),
        group_key=group_key, group_name=destinations.label(group_key),
        poll_chat_id=chat_id, poll_topic_id=topic_id,
    )
    _send_poll(session_id)
    export_schedule.schedule_export(session_id)

    # Confirmation lands in the same topic as the poll when /sendpoll was
    # invoked from the configured group. If invoked from a DM, reply in the
    # DM as before — Telegram rejects message_thread_id outside supergroups.
    chat_id = msg["chat"]["id"]
    in_group = chat_id == config.attendance_poll_chat_id()
    telegram.send_message(
        chat_id,
        f"✅ Poll sent to {destinations.label(group_key)} for {session_date}. (Session ID: {session_id})",
        message_thread_id=config.attendance_poll_topic_id() if in_group else None,
    )


def _selected_session(msg, args):
    from shared import destinations
    try:
        return db.get_latest_session(destinations.key(' '.join(args)))
    except ValueError as exc:
        telegram.send_message(msg['chat']['id'], str(exc))
        return None


def _cmd_skipsession(msg: dict, args: list[str]) -> None:
    session = _selected_session(msg, args)
    if session is None:
        telegram.send_message(msg["chat"]["id"], "No sessions found.")
        return
    db.mark_session_skipped(session["id"])
    if session.get('kind') == 'event':
        from shared import booking_store
        booking_store.purge_event_pii(session["id"])  # No export will run for it.
    # The export schedule still fires; the exporter checks the skipped flag
    # and no-ops.
    telegram.send_message(
        msg["chat"]["id"],
        f"⏭ Session {session['id']} ({session['session_date']}) marked as skipped.",
    )


def _cmd_setsession(msg: dict, args: list[str]) -> None:
    # Optional trailing --group NAME keeps the existing date/time/location syntax.
    group_args = []
    if '--group' in args:
        pos = args.index('--group')
        group_args, args = args[pos + 1:], args[:pos]
        if not group_args:
            telegram.send_message(msg['chat']['id'], 'Missing group after --group. Nothing changed.')
            return
    if len(args) < 4:
        telegram.send_message(
            msg["chat"]["id"],
            "Usage: /setsession YYYY-MM-DD START_HH:MM END_HH:MM Location\n"
            "Example: /setsession 2026-04-29 18:30 21:30 SIT @ Punggol Coast",
        )
        return

    session_date, start_time, end_time, *loc_parts = args
    location = " ".join(loc_parts)

    try:
        datetime.strptime(session_date, "%Y-%m-%d")
        datetime.strptime(start_time, "%H:%M")
        datetime.strptime(end_time, "%H:%M")
    except ValueError:
        telegram.send_message(
            msg["chat"]["id"],
            "❌ Invalid format. Date must be YYYY-MM-DD, times must be HH:MM.",
        )
        return

    session = _selected_session(msg, group_args)
    if session is None:
        telegram.send_message(
            msg["chat"]["id"],
            "No session to update. Use /sendpoll to create one first.",
        )
        return

    db.update_session_details(session["id"], session_date, start_time, end_time, location)
    # Move the session's export schedule to the new fire time.
    export_schedule.schedule_export(session["id"])

    telegram.send_message(
        msg["chat"]["id"],
        f"✅ Session {session['id']} updated:\n"
        f"  Date:     {session_date}\n"
        f"  Time:     {start_time} – {end_time}\n"
        f"  Location: {location}",
    )


def _cmd_summary(msg: dict, args: list[str]) -> None:
    session = _selected_session(msg, args)
    if session is None:
        telegram.send_message(msg["chat"]["id"], "No sessions found.")
        return

    labels = poll.category_labels()
    if session.get('kind') == 'event':
        labels = {'sit_student': 'SIT students', 'non_sit': 'Alumni / External', 'rental_skates': 'Rental skates'}
    lines = [
        f"\U0001f4cb Summary — {session.get('title') or session['session_date']} "
        f"{session['session_date']} ({session['location']})",
        f"Session ID: {session['id']}",
        f"Group: {session.get('group_name', 'SIT Inline Skate')}",
        "",
    ]
    total_ids: set[int] = set()
    for cat_key in poll.CATEGORY_KEYS:
        responses = db.get_responses_by_category(session["id"], cat_key)
        names = [r.get("first_name", "") for r in responses]
        total_ids.update(r["telegram_id"] for r in responses)
        lines.append(f"{labels[cat_key]} ({len(names)}):")
        if names:
            lines.extend(f"  • {n}" for n in names)
        else:
            lines.append("  (none)")
        lines.append("")
    lines.append(f"\U0001f465 {len(total_ids)} unique respondents")
    telegram.send_message(msg["chat"]["id"], "\n".join(lines))


def _cmd_importmembers(msg: dict, args: list[str]) -> None:
    if msg.get('chat', {}).get('type') != 'private':
        telegram.send_message(msg['chat']['id'], 'Please import the registry in a private chat with the bot.')
        return
    doc = msg.get("document") or (msg.get('reply_to_message') or {}).get('document')
    if doc is None:
        telegram.send_message(
            msg["chat"]["id"],
            "\U0001f4ce Please send the Excel file (.xlsx) as a document attachment "
            "together with /importmembers.",
        )
        return
    if not (doc.get("file_name") or "").endswith(".xlsx"):
        telegram.send_message(msg["chat"]["id"], "❌ File must be a .xlsx Excel file.")
        return

    telegram.send_message(msg["chat"]["id"], "⏳ Downloading and importing...")

    file_info = telegram.get_file(doc["file_id"])
    file_path = file_info["file_path"]

    # Lambda provides a writable /tmp; tempfile uses it by default.
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        telegram.download_file(file_path, tmp_path)
        counts = importer.import_from_file(tmp_path)
    except ValueError as exc:
        telegram.send_message(msg['chat']['id'], f'Import rejected: {exc}')
        return
    finally:
        with suppress(OSError):
            os.unlink(tmp_path)

    total_in_db = db.count_members()
    telegram.send_message(
        msg["chat"]["id"],
        f"✅ Import complete!\n"
        f"  New:       {counts['new']}\n"
        f"  Updated:   {counts['updated']}\n"
        f"  Unchanged: {counts['unchanged']}\n"
        f"  Skipped:   {counts['skipped']} (missing name or handle)\n"
        f"  Rows read: {counts['total']}\n\n"
        f"Acknowledged: {counts.get('acknowledged', 0)}; ambiguous: {counts.get('ambiguous', 0)}\n"
        f"Total members in database: {total_in_db}",
    )


def _cmd_rentals(msg: dict, args: list[str]) -> None:
    if msg.get('chat', {}).get('type') != 'private':
        telegram.send_message(msg['chat']['id'], 'Please request the rental list in a private chat.')
        return
    from shared import booking_flow, rentals
    session = db.get_session(args[0]) if len(args) == 1 and _ULID_RE.fullmatch(args[0]) else _selected_session(msg, args)
    if session is None:
        telegram.send_message(msg['chat']['id'], 'No session found.')
        return
    state = booking_flow.state_for(session['id'])
    stock = rentals.available(state)
    lines = [f"Rentals: {session.get('group_name', 'SIT Inline Skate')} / {session['session_date']} ({session['id']})", 'Remaining pairs: ' + ', '.join(f'{k}: {v}' for k, v in stock.items())]
    for uid, person in state['people'].items():
        rental = person.get('rental')
        if rental:
            lines.append(f"{person.get('first_name', uid)} (@{person.get('username') or 'no handle'}, ID {uid}): "
                         f"EU {rental['requested']} -> {rental['allocated']}; guards {'Yes' if rental['guards'] else 'No'}")
        if person.get('review'):
            lines.append(f"REVIEW: {person.get('first_name', uid)} / ID {uid}: {person['review']}")
    chunk = ''
    for line in lines:
        if len(chunk) + len(line) > 3500:
            telegram.send_message(msg['chat']['id'], chunk)
            chunk = ''
        chunk += line + '\n'
    if chunk:
        telegram.send_message(msg['chat']['id'], chunk)


def _cmd_linkmember(msg: dict, args: list[str]) -> None:
    """Admin resolves changed/missing handles using an existing acknowledged entry."""
    if msg.get('chat', {}).get('type') != 'private':
        return
    if len(args) != 2 or not args[0].isdigit() or not re.fullmatch(r'@?[A-Za-z][A-Za-z0-9_]{4,31}', args[1]):
        telegram.send_message(msg['chat']['id'], 'Usage: /linkmember TELEGRAM_USER_ID REGISTERED_HANDLE')
        return
    from shared import booking_store
    member = db.get_member(args[1])
    if not member or not member.get('rules_acknowledged') or member.get('ambiguous'):
        telegram.send_message(msg['chat']['id'], 'An unambiguous acknowledged registry entry is required.')
        return
    if not booking_store.claim_member(member['username'], args[0]):
        telegram.send_message(msg['chat']['id'], 'This registry entry is already linked to another Telegram account.')
        return
    booking_store.save_profile(int(args[0]), member_username=member['username'])
    telegram.send_message(msg['chat']['id'], 'Registry entry linked. The user can check again.')


def _cmd_sessions(msg: dict, args: list[str]) -> None:
    from shared import session_admin
    session_admin.list_sessions(msg, args)


def _cmd_deletesession(msg: dict, args: list[str]) -> None:
    from shared import session_admin
    session_admin.delete_session(msg, args)


def _cmd_specialevent(msg: dict, args: list[str]) -> None:
    from shared import events
    events.begin_setup(msg)


def _cmd_eventqr(msg: dict, args: list[str]) -> None:
    from shared import events
    events.qr_command(msg, args)


_COMMAND_HANDLERS = {
    "sessions": _cmd_sessions,
    "deletesession": _cmd_deletesession,
    "rentals": _cmd_rentals,
    "linkmember": _cmd_linkmember,
    "sendpoll": _cmd_sendpoll,
    "skipsession": _cmd_skipsession,
    "setsession": _cmd_setsession,
    "summary": _cmd_summary,
    "importmembers": _cmd_importmembers,
    "specialevent": _cmd_specialevent,
    "eventqr": _cmd_eventqr,
}


# ── Callback query (button tap) ─────────────────────────────────────────────

def _handle_callback(cq: dict, update_id: int = -1) -> None:
    data = cq.get("data") or ""

    if getattr(config, 'BOOKINGS_ENABLED', False):
        from shared import booking_flow
        if update_id < 0:
            telegram.answer_callback_query(cq['id'], 'Missing update identifier. Please try again.')
            return
        if data.startswith('e:'):
            from shared import events
            events.callback(cq, update_id)
        elif data.startswith('r:'):
            booking_flow.callback(cq, update_id)
        else:
            booking_flow.attendance(cq, update_id)
        return

    parts = data.split(":")
    if len(parts) != 3 or parts[0] != "vote":
        telegram.answer_callback_query(cq["id"], "This button is not recognized.")
        return
    session_id, category = parts[1], parts[2]

    # Reject anything that doesn't match the expected shape before it reaches
    # the database.
    if not _ULID_RE.match(session_id) or category not in poll.CATEGORY_KEYS:
        log.warning("Rejected callback with invalid session_id/category: %r", data)
        telegram.answer_callback_query(cq["id"], "This poll button is no longer valid.")
        return

    user = cq.get("from") or {}
    user_id = user.get("id")
    if user_id is None:
        log.warning("Callback missing from.id — dropping (would corrupt shared 'None' row).")
        telegram.answer_callback_query(cq["id"], "Could not identify your Telegram account.")
        return
    now_iso = datetime.now(UTC).isoformat()

    session = db.get_session(session_id)
    if session is None:
        telegram.answer_callback_query(cq['id'], 'This session no longer exists.')
        return

    added = db.toggle_response(
        session_id=session_id,
        telegram_id=user_id,
        username=user.get("username"),
        first_name=user.get("first_name") or user.get("username") or str(user_id),
        category=category,
        responded_at=now_iso,
    )

    session = db.get_session(session_id)
    if session is None:
        telegram.answer_callback_query(cq["id"], "This session no longer exists.")
        return

    responses = db.get_responses(session_id)
    message = cq.get("message") or {}
    chat_id = (message.get("chat") or {}).get("id")
    message_id = message.get("message_id")
    if chat_id is None or message_id is None:
        telegram.answer_callback_query(cq["id"], "Recorded, but I could not update this message.")
        return

    try:
        telegram.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=poll.build_poll_text(session, responses),
            reply_markup=poll.build_keyboard(session_id),
        )
    except Exception:
        log.exception("Recorded callback but failed to edit poll message.")
        telegram.answer_callback_query(cq["id"], "Recorded, but I could not update the poll message.")
        return
    telegram.answer_callback_query(cq["id"], "Added." if added else "Removed.")


# ── Poll send + export-job orchestration ────────────────────────────────────

def _send_poll(session_id: str) -> None:
    """Send a fresh poll to the group and persist the message_id for later edits."""
    session = db.get_session(session_id)
    if session is None:
        return
    result = telegram.send_message(
        chat_id=session.get('poll_chat_id') or config.attendance_poll_chat_id(),
        text=poll.build_poll_text(session, []),
        reply_markup=poll.build_keyboard(session_id, session),
        message_thread_id=(session['poll_topic_id'] or None) if 'poll_topic_id' in session else config.attendance_poll_topic_id(),
    )
    db.set_poll_message_id(session_id, result["message_id"], session.get('poll_chat_id') or config.attendance_poll_chat_id())
