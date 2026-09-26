"""Admin-only, private-chat session listing and recoverable deletion."""
from __future__ import annotations

import logging
import re

from shared import booking_store, config, db, telegram

log = logging.getLogger(__name__)
SESSION_ID = re.compile(r'^[0-9A-HJKMNP-TV-Z]{26}$')
PAGE_SIZE = 8


def authorized_private(msg):
    user_id = (msg.get('from') or {}).get('id')
    chat = msg.get('chat') or {}
    if user_id not in config.admin_ids():
        return False
    if chat.get('type') != 'private' or chat.get('id') != user_id:
        telegram.send_message(chat['id'], 'Please manage sessions in your private chat with the bot.')
        return False
    return True


def list_sessions(msg, args):
    if not authorized_private(msg):
        return
    chat_id = msg['chat']['id']
    if len(args) > 1 or (args and (not args[0].isdigit() or len(args[0]) > 6 or int(args[0]) < 1)):
        telegram.send_message(chat_id, 'Usage: /sessions [PAGE_NUMBER]')
        return
    sessions = db.list_sessions_for_admin()
    if not sessions:
        telegram.send_message(chat_id, 'No sessions found. Deleted sessions are hidden.')
        return
    page = int(args[0]) if args else 1
    pages = (len(sessions) + PAGE_SIZE - 1) // PAGE_SIZE
    if page > pages:
        telegram.send_message(chat_id, f'No such page. Use /sessions {pages} for the last page.')
        return
    lines = [f'Sessions (page {page}/{pages})', '']
    for session in sessions[(page - 1) * PAGE_SIZE:page * PAGE_SIZE]:
        status = 'exported' if session.get('exported') else ('skipped' if session.get('skipped') else 'not exported')
        lines.extend([f"{session['session_date']} {session['start_time']} - {session['end_time']} ({status})",
                      str(session.get('location', ''))[:120], session['id'], ''])
    lines.append('To preview deletion: /deletesession SESSION_ID')
    if page < pages:
        lines.append(f'Next page: /sessions {page + 1}')
    telegram.send_message(chat_id, '\n'.join(lines))


def remove_poll(session):
    message_id = session.get('poll_message_id')
    if not message_id:
        return 'No group poll was recorded.'
    # Old sessions lack poll_chat_id. Never guess a destructive target from the
    # current config: the bot may have moved groups since the poll was created.
    chat_id = session.get('poll_chat_id')
    if not chat_id:
        return 'Original poll chat was not recorded. Remove that message manually; its booking actions are blocked.'
    try:
        telegram.delete_message(chat_id, message_id)
        return 'Group poll removed.'
    except Exception:
        log.warning('Poll deletion unavailable for session %s; trying a closed notice', session['id'])
    try:
        telegram.edit_message_text(chat_id, message_id,
            f"Session {session['session_date']} was deleted by an admin. Bookings are closed.",
            reply_markup={'inline_keyboard': []})
        return 'Telegram could not delete the poll; it was replaced with a closed notice and no buttons.'
    except Exception:
        log.warning('Poll cleanup unavailable for deleted session %s', session['id'])
        return 'Poll cleanup failed. Booking actions are blocked; remove the message manually or repeat the confirmation command.'


def delete_session(msg, args):
    if not authorized_private(msg):
        return
    chat_id = msg['chat']['id']
    if len(args) not in (1, 2) or not SESSION_ID.fullmatch(args[0]) or (len(args) == 2 and args[1] != 'CONFIRM'):
        telegram.send_message(chat_id, 'Usage: /deletesession SESSION_ID\nUse /sessions to find the exact ID. No session was deleted.')
        return
    sid = args[0]
    session = db.get_session(sid, include_deleted=True)
    if session is None:
        telegram.send_message(chat_id, 'Session not found. No session was deleted.')
        return
    if len(args) == 1:
        responses = db.get_responses(sid)
        people = len({row['telegram_id'] for row in responses})
        rentals = sum(row.get('category') == 'rental_skates' for row in responses)
        telegram.send_message(chat_id,
            f"Delete session {session['session_date']} {session['start_time']} - {session['end_time']}?\n"
            f"Location: {str(session.get('location', ''))[:180]}\nID: {sid}\n"
            f"Attendees: {people}; rentals: {rentals}.\n\n"
            'This hides the session, blocks bookings and queued exports, and attempts to remove its group poll. '
            'Records are retained internally for recovery. Deleted Telegram messages cannot be restored. '
            'Existing CSV/Excel reports are not removed; an export already running may finish. '
            'Deleting an upcoming session lets the normal scheduler create a replacement.\n\n'
            f'To confirm this exact session, send:\n/deletesession {sid} CONFIRM\n'
            'Otherwise, do nothing. No change has been made by this preview.')
        return
    try:
        db.mark_session_deleted(sid, msg['from']['id'])
        booking_store.close_deleted_session(sid)
    except Exception:
        log.exception('Session deletion cleanup incomplete for %s', sid)
        telegram.send_message(chat_id, 'Deletion did not fully finish. The session may already be hidden. '
                              f'Retry this exact command:\n/deletesession {sid} CONFIRM')
        return
    cleanup = remove_poll(session)
    telegram.send_message(chat_id,
        f"Session {session['session_date']} deleted from the bot.\nID: {sid}\n{cleanup}\n"
        'Session/attendance records are retained internally for recovery. Member registration is unchanged. '
        'Existing CSV/Excel reports are unchanged; an export already running may finish.')
