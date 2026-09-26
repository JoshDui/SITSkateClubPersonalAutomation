"""Private Telegram rental flow and registry-gated attendance."""
from __future__ import annotations

import logging
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from shared import booking_store as store, config, db, poll, rentals, telegram

log = logging.getLogger(__name__)
SESSION_ID = re.compile(r'^[0-9A-HJKMNP-TV-Z]{26}$')


def button(text, sid, action, *args):
    data = ':'.join(map(str, ['r', sid, action, *args]))
    if len(data.encode()) > 64:
        raise ValueError('Callback exceeds Telegram limit')
    return {'text': text, 'callback_data': data}


def send(user, text, rows=()):
    return telegram.send_message(user['id'], text, reply_markup={'inline_keyboard': list(rows)})


def back_to_group(sid):
    session = db.get_session(sid) or {}
    chat = str(session.get('poll_chat_id') or config.attendance_poll_chat_id())
    message = session.get('poll_message_id')
    if chat.startswith('-100') and message:
        return [[{'text': 'Back to group poll', 'url': f'https://t.me/c/{chat[4:]}/{message}'}]]
    return []


def session_open(sid):
    if not SESSION_ID.fullmatch(sid):
        raise rentals.BookingError('Invalid session link.')
    session = db.get_session(sid)
    if not session or session.get('skipped') or session.get('exported'):
        raise rentals.BookingError('This session is closed or cancelled.')
    starts = datetime.fromisoformat(f"{session['session_date']}T{session['start_time']}").replace(tzinfo=ZoneInfo(config.TIMEZONE))
    if datetime.now(ZoneInfo(config.TIMEZONE)) >= starts:
        raise rentals.BookingError('Online booking changes close when training starts.')
    return session


def state_for(sid):
    return store.read(sid) or rentals.initial_state(db.get_legacy_responses(sid))


def eligible(user, sid, rental=True):
    if not store.acknowledged_member(user):
        rows = []
        if config.RULES_FORM_URL:
            rows.append([{'text': 'Complete rules form', 'url': config.RULES_FORM_URL}])
        rows += [[button('Check again', sid, 'menu' if rental else 'check')],
                 [button('Ask an admin', sid, 'help')]]
        send(user, 'A completed rules acknowledgement matching your Telegram account is required. '
             'Complete the form using your current Telegram handle. '
             'Check again checks the latest imported registry; a new submission may need an admin to refresh it.', rows)
        return False
    if rental and state_for(sid)['people'].get(str(user['id']), {}).get('current_student') is not True:
        send(user, 'Rentals are for currently enrolled SIT students only. Are you currently enrolled?', [
            [button('Yes, currently enrolled', sid, 'student')],
            [button('No / alumnus', sid, 'external')],
        ])
        return False
    return True


def menu(user, sid):
    session = session_open(sid)
    if not eligible(user, sid):
        return
    state = state_for(sid)
    person = state['people'].get(str(user['id']), {})
    version = person.get('version', 0)
    stock = rentals.available(state, user['id'])
    sizes = [button(f'EU {s} · {stock[s]} exact / {stock.get(s+1, 0)} larger', sid, 'size', version, s)
             for s in rentals.INVENTORY]
    rows = [sizes[i:i+2] for i in range(0, len(sizes), 2)]
    rows.append([button('Other / unsure — admin help', sid, 'help')])
    if person.get('rental'):
        rows += [[button('Cancel rental only', sid, 'cancelr', version)],
                 [button('Cancel attendance', sid, 'cancela', version)]]
    send(user, f"Choose your normal EU shoe size for {session['session_date']}.\n"
         'Larger means one EU size up, offered with your consent. Counts share stock and cannot be added together. '
         'Stock is checked again when you confirm. Your existing pair, if any, stays reserved until a change succeeds.', rows)


def refresh_poll(sid):
    session = db.get_session(sid)
    if not session:
        raise rentals.BookingError('This session is closed or deleted.')
    if not session.get('poll_message_id'):
        raise rentals.BookingError('Your selection was saved, but the group poll could not be updated: '
                                   'its message reference is missing. Please ask an admin to repair the poll.')
    for _ in range(3):
        before = db.get_responses(sid)
        telegram.edit_message_text(session.get('poll_chat_id') or config.attendance_poll_chat_id(),
                                   session['poll_message_id'], poll.build_poll_text(session, before),
                                   poll.build_keyboard(sid))
        # A deletion can race with the HTTP edit after this function's initial
        # read. Do not leave a restored keyboard over the admin's closed notice.
        if db.get_session(sid) is None:
            telegram.edit_message_text(session.get('poll_chat_id') or config.attendance_poll_chat_id(),
                session['poll_message_id'], 'This session was deleted. Bookings are closed.',
                reply_markup={'inline_keyboard': []})
            return
        if before == db.get_responses(sid):
            break


def change(sid, user, action, update_id, **kwargs):
    def operation(state):
        session_open(sid)
        return rentals.apply_action(state, user, action, update_id, **kwargs)
    state = store.mutate(sid, operation)
    # A failed edit must reach the webhook's retry response. Replayed updates
    # are idempotent, but will retry this edit so saved bookings reach the poll.
    refresh_poll(sid)
    return state


def start(user, sid, rules_only=False):
    try:
        session_open(sid)
        if rules_only:
            if eligible(user, sid, rental=False):
                send(user, 'Rules acknowledgement found. Return to the group and select your attendance option.')
        else:
            menu(user, sid)
    except rentals.BookingError as exc:
        send(user, str(exc))


def callback(cq, update_id):
    user = cq.get('from') or {}
    parts = (cq.get('data') or '').split(':')
    if not user.get('id') or len(parts) < 3 or not SESSION_ID.fullmatch(parts[1]):
        telegram.answer_callback_query(cq['id'], 'Invalid booking button.')
        return
    sid, action = parts[1:3]
    message = cq.get('message') or {}
    if message.get('chat', {}).get('type') != 'private' or message.get('chat', {}).get('id') != user['id']:
        telegram.answer_callback_query(cq['id'], 'Please use your private chat with the bot.')
        return
    try:
        telegram.answer_callback_query(cq['id'])
    except Exception:
        # Retried updates can outlive Telegram's callback acknowledgement window.
        log.warning('Callback acknowledgement unavailable; continuing durable action')
    try:
        session_open(sid)
        if action == 'help':
            change(sid, user, 'review', update_id)
            send(user, 'Your request is in the admin review list. No pair has been reserved by this request.')
            return
        if action in ('cancelr', 'cancela') and len(parts) == 4:
            change(sid, user, 'cancel_rental' if action == 'cancelr' else 'cancel_attendance',
                   update_id, version=int(parts[3]))
            send(user, 'Rental cancelled; attendance retained.' if action == 'cancelr' else 'Attendance and rental cancelled.')
            return
        if not eligible(user, sid, rental=False):
            return
        if action == 'check':
            send(user, 'Rules acknowledgement found. Return to the group and select your attendance option.')
            return
        if action in ('student', 'external'):
            change(sid, user, 'declare_student' if action == 'student' else 'declare_external', update_id)
            if action == 'external':
                send(user, 'Alumni and non-SIT participants cannot rent. You can register non-SIT attendance with your own skates.')
                return
        if not eligible(user, sid):
            return
        if action in ('menu', 'student'):
            menu(user, sid)
            return
        if action == 'size' and len(parts) == 5:
            version, requested = map(int, parts[3:])
            state = state_for(sid)
            if version != state['people'].get(str(user['id']), {}).get('version', 0):
                raise rentals.BookingError('This menu is out of date. Open it again.')
            stock = rentals.available(state, user['id'])
            if requested not in rentals.INVENTORY:
                raise rentals.BookingError('Please ask an admin about this size.')
            allocated = requested if stock[requested] > 0 else requested + 1
            if stock.get(allocated, 0) < 1:
                send(user, 'No exact or one-size-larger pair is available.', [[button('Ask an admin', sid, 'help')], [button('Choose again', sid, 'menu')]])
                return
            text = f'EU {allocated} is available.' if requested == allocated else f'EU {requested} is unavailable. Would you accept EU {allocated} (one size larger)?'
            send(user, text, [[button('Continue' if requested == allocated else 'Yes, accept larger size', sid, 'guards', version, requested, allocated)], [button('Choose again', sid, 'menu')]])
        elif action == 'guards' and len(parts) == 6:
            version, requested, allocated = map(int, parts[3:])
            send(user, f'Reserve EU {allocated} for your EU {requested} request. Do you need guards? '
                 'Your next selection confirms the reservation.', [
                [button('Yes — confirm rental', sid, 'book', version, requested, allocated, 1)],
                [button('No — confirm rental', sid, 'book', version, requested, allocated, 0)],
            ])
        elif action == 'book' and len(parts) == 7:
            version, requested, allocated, guards = map(int, parts[3:])
            if guards not in (0, 1):
                raise rentals.BookingError('Invalid guards choice.')
            state = change(sid, user, 'reserve', update_id, version=version,
                           requested=requested, allocated=allocated, guards=bool(guards))
            person = state['people'][str(user['id'])]
            rental = person.get('rental')
            if not rental:
                raise rentals.BookingError('This reservation was already cancelled. Open the menu again.')
            send(user, f"Reserved EU {rental['allocated']} (requested EU {rental['requested']}). "
                 f"Guards: {'Yes' if rental['guards'] else 'No'}. You are registered as attending.", [
                [button('Change size', sid, 'menu')],
                [button('Cancel rental only', sid, 'cancelr', person['version'])],
                [button('Cancel attendance', sid, 'cancela', person['version'])],
            ] + back_to_group(sid))
        else:
            raise rentals.BookingError('Unknown selection. Open the rental menu again.')
    except (rentals.BookingError, ValueError) as exc:
        send(user, str(exc), [[button('Open size menu', sid, 'menu')]])


def attendance(cq, update_id):
    user = cq.get('from') or {}
    parts = (cq.get('data') or '').split(':')
    if len(parts) != 3 or parts[0] != 'vote' or not user.get('id'):
        telegram.answer_callback_query(cq['id'], 'Invalid attendance button.')
        return
    sid, category = parts[1:]
    try:
        session = session_open(sid)
        message = cq.get('message') or {}
        if (message.get('chat', {}).get('id') != (session.get('poll_chat_id') or config.attendance_poll_chat_id())
                or message.get('message_id') != session.get('poll_message_id')):
            raise rentals.BookingError('Please use the original group poll.')
        if category == 'rental_skates':
            telegram._call('answerCallbackQuery', callback_query_id=cq['id'],
                           url=f'https://t.me/{config.BOT_USERNAME}?start=rent_{sid}')
            return
        if category not in ('sit_student', 'non_sit'):
            raise rentals.BookingError('Invalid attendance category.')
        state = state_for(sid)
        person = state['people'].get(str(user['id']), {})
        withdrawing = person.get('attendance') == category and not person.get('rental')
        if not withdrawing and not store.acknowledged_member(user):
            telegram._call('answerCallbackQuery', callback_query_id=cq['id'],
                           url=f'https://t.me/{config.BOT_USERNAME}?start=rules_{sid}')
            return
        change(sid, user, category, update_id)
        try:
            telegram.answer_callback_query(cq['id'], 'Attendance updated.')
        except Exception:
            log.warning('Attendance saved; callback acknowledgement unavailable')
    except rentals.BookingError as exc:
        try:
            telegram.answer_callback_query(cq['id'], str(exc)[:190])
        except Exception:
            log.warning('Attendance rejected; callback acknowledgement unavailable')
