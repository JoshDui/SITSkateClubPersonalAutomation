"""Special events: the admin setup form (/specialevent) and participant registration.

Both are step-by-step forms in the bot's private chat. A form lives in the
state table (shared/booking_store.py) for at most 30 minutes; buttons carry a
per-form nonce so a stale button from an older form cannot act on a new one.

An event is a session with kind='event' and group_key='event': it is posted to
the SIT announcements topic but never counts as a SIT training session, so it
cannot suppress the weekly poll. Registrants are stored in the session's
booking document with attendance 'sit_student' (SIT) or 'non_sit' (alumni), so
the existing rental flow, stock counts and announcement refreshes apply.

Personal data (full names, student numbers, partial NRIC/FIN, cluster,
programme, year) is only shown to the registrant and in the admins' CSV. It is
never logged; messages that contain (or may contain) a student number or ID
are deleted from the chat; and it is removed from the database after the CSV
export, when an event is skipped or deleted, and by the scheduler's sweep.
"""
from __future__ import annotations

import logging
import secrets
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from shared import booking_flow, config, db, export_schedule, poll, programmes, rentals, telegram
from shared import booking_store as store
from shared import event_forms as ef

log = logging.getLogger(__name__)

SETUP_STEPS = ('title', 'date', 'start', 'end', 'location', 'details')
SETUP_PROMPTS = {
    'title': 'What is the event called? (e.g. Halloween Skate)',
    'date': 'What date is it? Send YYYY-MM-DD, e.g. 2026-10-31.',
    'start': 'What time does it start? Send 24-hour HH:MM, e.g. 19:00.',
    'end': 'What time does it end? Send 24-hour HH:MM, e.g. 22:00.',
    'location': 'Where is it held?',
    'details': f'Send the event details shown on the announcement (up to {ef.DETAILS_MAX} characters).',
}
CATEGORY_LABELS = {'sit': 'SIT student', 'alumni': 'Alumni / External'}
NOT_LISTED = 'Programme not listed'
EXPIRED = 'This form has expired or was replaced. Please start again.'


def _now():
    return datetime.now(ZoneInfo(config.TIMEZONE))


def _button(text, nonce, action, arg=''):
    data = f'e:{nonce}:{action}' + (f':{arg}' if arg != '' else '')
    if len(data.encode()) > 64:
        raise ValueError('Callback exceeds Telegram limit')
    return {'text': text, 'callback_data': data}


def _send(chat_id, text, rows=()):
    markup = {'inline_keyboard': [list(row) for row in rows]} if rows else None
    return telegram.send_message(chat_id, text, reply_markup=markup)


def _new_form(**fields):
    return {'nonce': secrets.token_hex(3), 'data': {}, **fields}


def _save(user_id, form):
    if store.save_form(user_id, form):
        return True
    _send(user_id, 'That clashed with another of your answers. Please send it again.')
    return False


def _summary_lines(session):
    return [session.get('title') or 'Special event',
            f"{poll._format_date(session['session_date'])}, "
            f"{poll._format_time(session['start_time'])} - {poll._format_time(session['end_time'])}",
            session['location']]


def _event_session(sid):
    """Open event session or BookingError (closed, deleted, started, not an event)."""
    session = booking_flow.session_open(sid)
    if session.get('kind') != 'event':
        raise rentals.BookingError('This link is not for a special event.')
    return session


def _delete_message(msg):
    """Remove a message that contains, or may contain, a student number or ID."""
    try:
        telegram.delete_message(msg['chat']['id'], msg['message_id'])
    except Exception:
        log.warning('Could not delete a registration message')


def cancel_form(user):
    had_form = store.get_form(user['id']) is not None
    store.delete_form(user['id'])
    _send(user['id'], 'Cancelled. Nothing was saved.' if had_form else 'There is nothing to cancel.')


def _prompt(uid, form):
    """(Re)send the question for the form's current step."""
    nonce, step, data = form['nonce'], form['step'], form['data']
    if form['kind'] == 'setup':
        if step != 'preview':
            _send(uid, SETUP_PROMPTS[step])
            return
        preview = poll.build_event_text({
            'title': data['title'], 'session_date': data['date'], 'start_time': data['start'],
            'end_time': data['end'], 'location': data['location'], 'details': data['details'],
        }, [])
        _send(uid, 'Preview of the announcement:\n\n' + preview, [
            [_button('Post to SIT announcements', nonce, 'post')],
            [_button('Start over', nonce, 'restart'), _button('Cancel', nonce, 'cancel')],
        ])
    elif step == 'name':
        _send(uid, 'What is your full name?')
    elif step == 'student_no':
        _send(uid, 'What is your SIT student number? (7 digits; your message will be removed '
                   'from this chat once read)')
    elif step == 'masked_id':
        _send(uid, 'For campus security, send ONLY the first letter, the last 3 digits and the '
                   'last letter of your NRIC/FIN, e.g. T567D. Do not send the full number. '
                   '(Your message will be removed from this chat once read.)')
    elif step == 'cluster':
        _send(uid, 'Which cluster is your programme in?',
              [[_button(c, nonce, 'cluster', c) for c in programmes.CLUSTER_ORDER]])
    elif step == 'programme':
        cluster = data['cluster']
        rows = [[_button(label, nonce, 'prog', i)]
                for i, (_, label) in enumerate(programmes.PROGRAMMES[cluster])]
        rows += [[_button('Not listed', nonce, 'prog', 'none')],
                 [_button('‹ Back to clusters', nonce, 'prog', 'back')]]
        _send(uid, f'Which programme? ({cluster})', rows)
    elif step == 'year':
        _send(uid, 'Which year of study are you in?', [
            [_button(ef.YEARS[k], nonce, 'year', k) for k in ('1', '2', '3', '4')],
            [_button('Other', nonce, 'year', 'other')],
        ])
    elif step == 'skates':
        _send(uid, 'Do you need to rent skates? (You will choose your size next.)', [
            [_button('Yes, I need skates', nonce, 'skates', 1), _button('No', nonce, 'skates', 0)],
        ])
    elif step == 'confirm':
        lines = [f"Category: {CATEGORY_LABELS[form['category']]}", f"Name: {data['name']}"]
        if form['category'] == 'sit':
            lines += [f"Student number: {data['student_no']}", f"Cluster: {data['cluster']}",
                      f"Programme: {data.get('programme') or NOT_LISTED}",
                      f"Year of study: {data['year']}",
                      f"Rental skates: {'Yes' if data['skates'] else 'No'}"]
        else:
            lines.append(f"Partial NRIC/FIN: {data['masked_id']}")
        _send(uid, 'Please check your registration:\n\n' + '\n'.join(lines), [
            [_button('Confirm registration', nonce, 'confirm')],
            [_button('Start over', nonce, 'restart'), _button('Cancel', nonce, 'cancel')],
        ])


# ── Admin: /specialevent ─────────────────────────────────────────────────────

def begin_setup(msg):
    uid = msg['from']['id']
    store.save_form(uid, _new_form(kind='setup', step='title'))
    _send(uid, 'New special event. It will be posted to the SIT announcements topic after you '
               'confirm a preview. Send /cancel at any time.\n\n' + SETUP_PROMPTS['title'])


def _setup_answer(uid, form, text):
    step, data = form['step'], form['data']
    try:
        if step == 'title':
            data['title'] = ef.title(text)
        elif step == 'date':
            data['date'] = ef.event_date(text, _now().date())
        elif step == 'start':
            start = ef.clock(text)
            ef.starts_in_future(data['date'], start, _now())
            data['start'] = start
        elif step == 'end':
            data['end'] = ef.end_after(data['start'], ef.clock(text))
        elif step == 'location':
            data['location'] = ef.location(text)
        elif step == 'details':
            data['details'] = ef.details(text)
        else:
            _send(uid, 'Please use the Post / Start over / Cancel buttons on the preview.')
            return
    except ef.FormError as exc:
        if _save(uid, form):  # Records the update so a retry is not re-processed.
            _send(uid, str(exc))
        return
    index = SETUP_STEPS.index(step)
    form['step'] = SETUP_STEPS[index + 1] if index + 1 < len(SETUP_STEPS) else 'preview'
    if _save(uid, form):
        _prompt(uid, form)


def _post(uid, nonce):
    form = store.get_form(uid)
    if not form or form.get('kind') != 'setup' or form['nonce'] != nonce or form['step'] != 'preview':
        _send(uid, EXPIRED)
        return
    if uid not in config.admin_ids():
        store.delete_form(uid)
        return
    data = store.take_form(uid)  # Only one of two simultaneous Post taps gets the form.
    if not data or data.get('nonce') != nonce:
        _send(uid, 'This event was already posted or cancelled.')
        return
    d = data['data']
    try:
        ef.starts_in_future(d['date'], d['start'], _now())
    except ef.FormError as exc:
        _send(uid, f'{exc} Nothing was posted; send /specialevent to start again.')
        return
    sid = db.create_session(
        d['date'], d['start'], d['end'], d['location'], created_at=datetime.now(UTC).isoformat(),
        group_key='event', group_name='Special event', kind='event',
        title=d['title'], details=d['details'],
        poll_chat_id=config.attendance_poll_chat_id(), poll_topic_id=config.attendance_poll_topic_id(),
    )
    try:
        # Export first: once people can register, their details must have a
        # scheduled export (and purge) behind them.
        export_schedule.schedule_export(sid)
        post_announcement(sid)
    except Exception:
        log.exception('Could not publish special event %s; withdrawing it', sid)
        db.mark_session_deleted(sid, uid)
        _send(uid, 'The event could not be posted (Telegram or AWS error), so it was withdrawn. '
                   'Nothing is visible to members. Please send /specialevent to try again.')
        return
    _send(uid, f"✅ Posted \"{d['title']}\" for {d['date']}.\nSession ID: {sid}\n\n"
               "Registrations: /summary Events\nRental skates: /rentals Events\n"
               f"Web sign-up QR code: /eventqr {sid}\nRemove it: /deletesession {sid}\n\n"
               "The attendance CSV is sent to admins 24 hours after the event starts; "
               "registration details are then deleted from the bot.")
    try:
        send_qr(uid, db.get_session(sid))
    except Exception:
        log.warning('Could not send the QR code for event %s', sid)
        _send(uid, f'The QR code could not be sent just now. Try /eventqr {sid}.')


# ── Web registration links and QR codes ──────────────────────────────────────

def web_link(session):
    """The event's public sign-up URL, or None if the web form is not set up."""
    base = (config.WEBFORM_BASE_URL or '').rstrip('/')
    if not base:
        return None
    from shared import web_tokens
    return f"{base}/e/{session['id']}?k={web_tokens.event_token(session['id'])}"


def send_qr(chat_id, session):
    link = web_link(session)
    if not link:
        _send(chat_id, 'Web registration is not available for this event.')
        return
    from shared import qr  # Only the webhook Lambda bundles segno.
    caption = (f"{session.get('title') or 'Special event'} ({session['session_date']})\n"
               f"Scan to register on the web, or open:\n{link}\n\n"
               "Anyone with this link can register until the event starts.")
    telegram.send_photo(chat_id, f"event-{session['session_date']}.png", qr.png(link), caption=caption)


def qr_command(msg, args):
    """/eventqr [SESSION_ID | Events]: resend an event's web sign-up QR code."""
    chat_id = msg['chat']['id']
    if len(args) == 1 and booking_flow.SESSION_ID.fullmatch(args[0]):
        session = db.get_session(args[0])
    elif not args or ' '.join(args).casefold() in ('event', 'events'):
        session = db.get_latest_session('event')
    else:
        _send(chat_id, 'Usage: /eventqr SESSION_ID (or /eventqr for the latest event)')
        return
    if not session or session.get('kind') != 'event':
        _send(chat_id, 'No special event found. Use /sessions Events to list them.')
        return
    send_qr(chat_id, session)


def post_announcement(sid):
    session = db.get_session(sid)
    result = telegram.send_message(
        chat_id=session['poll_chat_id'],
        text=poll.build_poll_text(session, []),
        reply_markup=poll.build_keyboard(sid, session),
        message_thread_id=session.get('poll_topic_id') or None,
    )
    db.set_poll_message_id(sid, result['message_id'], session['poll_chat_id'])


# ── Participants: registration ───────────────────────────────────────────────

def start_registration(user, sid, category):
    uid = user['id']
    try:
        session = _event_session(sid)
    except rentals.BookingError as exc:
        _send(uid, str(exc))
        return
    intro = '\n'.join(_summary_lines(session))
    person = booking_flow.state_for(sid)['people'].get(str(uid), {})
    if person.get('attendance') and person.get('event_form'):
        registered = CATEGORY_LABELS[person['event_form']['category']]
        rows = [[booking_flow.button('Cancel registration', sid, 'cancela', person['version'])]]
        if person['event_form']['category'] == 'sit':
            rows.insert(0, [booking_flow.button('Rental skates menu', sid, 'menu')])
        _send(uid, f'{intro}\n\nYou are registered as: {registered}.\n'
                   'To change your details, cancel and register again with the buttons on the '
                   'announcement.', rows + booking_flow.back_to_group(sid))
        return
    existing = store.get_form(uid)
    if (existing and existing.get('kind') == 'register' and existing.get('sid') == sid
            and existing.get('category') == category):
        _prompt(uid, existing)  # A repeated link resumes rather than resets the form.
        return
    form = _new_form(kind='register', sid=sid, category=category, step='name')
    if not _save(uid, form):
        return
    _send(uid, f'{intro}\n\nRegistering as: {CATEGORY_LABELS[category]}. '
               'Your details are only shared with the club committee (and campus security for '
               'alumni and external participants) and are deleted from the bot after the event. Send /cancel at any time.')
    _prompt(uid, form)


def _register_answer(user, form, text, msg):
    uid, step, data = user['id'], form['step'], form['data']
    sit = form['category'] == 'sit'
    if step in ('student_no', 'masked_id') or (step == 'name' and any(ch.isdigit() for ch in text)):
        _delete_message(msg)  # Even when invalid: it may contain a full NRIC/FIN.
    try:
        if step == 'name':
            data['name'] = ef.full_name(text)
            form['step'] = 'student_no' if sit else 'masked_id'
        elif step == 'student_no':
            data['student_no'] = ef.student_number(text)
            form['step'] = 'cluster'
        elif step == 'masked_id':
            data['masked_id'] = ef.masked_id(text)
            form['step'] = 'confirm'
        else:
            _send(uid, 'Please use the buttons above.')
            return
    except ef.FormError as exc:
        if _save(uid, form):
            _send(uid, str(exc))
        return
    if _save(uid, form):
        _prompt(uid, form)


def _confirm(user, form, update_id):
    """Save the registration, then remove the form. If anything after the save
    fails, Telegram's retry of the same update finds the form still there; the
    booking write is idempotent per update_id, so the retry just finishes."""
    uid, sid, data = user['id'], form['sid'], form['data']
    submission = {'category': form['category'], 'name': data['name']}
    if form['category'] == 'sit':
        submission.update(student_no=data['student_no'], cluster=data['cluster'],
                          programme=data.get('programme'), year=data['year'],
                          skates=bool(data['skates']))
    else:
        submission['masked_id'] = data['masked_id']
    state = booking_flow.change(sid, user, 'event_register', update_id, form=submission)
    store.delete_form(uid)
    person = state['people'][str(uid)]
    if submission.get('skates'):
        _send(uid, 'You are registered. Now choose your rental skate size:')
        booking_flow.menu(user, sid)
        return
    _send(uid, "✅ You are registered. You'll appear on the announcement in the group.",
          [[booking_flow.button('Cancel registration', sid, 'cancela', person['version'])]]
          + booking_flow.back_to_group(sid))


def _register_button(user, form, action, arg, update_id):
    """Apply a registration button. Returns True when the form should be saved
    and the next question asked."""
    data = form['data']
    if action == 'cluster' and form['step'] == 'cluster' and arg in programmes.CLUSTER_ORDER:
        data['cluster'] = arg
        form['step'] = 'programme'
    elif action == 'prog' and form['step'] == 'programme':
        if arg == 'back':
            data.pop('cluster', None)
            form['step'] = 'cluster'
        elif arg == 'none':
            data['programme'] = None
            form['step'] = 'year'
        else:
            try:
                data['programme'] = programmes.programme(data['cluster'], int(arg))[0]
            except ValueError:
                raise ef.FormError('That programme button is out of date. Please choose again.') from None
            form['step'] = 'year'
    elif action == 'year' and form['step'] == 'year':
        data['year'] = ef.year_of_study(arg)
        form['step'] = 'skates'
    elif action == 'skates' and form['step'] == 'skates' and arg in ('0', '1'):
        data['skates'] = arg == '1'
        form['step'] = 'confirm'
    elif action == 'confirm' and form['step'] == 'confirm':
        _confirm(user, form, update_id)
        return False
    else:
        _send(user['id'], 'That button is out of date. Please use the latest message.')
        return False
    return True


# ── Dispatch ─────────────────────────────────────────────────────────────────

def handle_text(msg, update_id):
    """Route a non-command private message to the sender's form, if any."""
    user = msg.get('from') or {}
    uid = user.get('id')
    form = store.get_form(uid)
    if not form:
        return
    if update_id <= form.get('last_update', -1):
        _prompt(uid, form)  # A redelivered update: re-ask instead of going silent.
        return
    form['last_update'] = update_id
    text = msg.get('text')
    if text is None:
        if form['kind'] == 'register' and form['step'] in ('student_no', 'masked_id'):
            _delete_message(msg)
        _send(uid, 'Please reply with text.')
        return
    try:
        if form['kind'] == 'setup':
            if uid not in config.admin_ids():
                store.delete_form(uid)
                return
            _setup_answer(uid, form, text)
        else:
            _event_session(form['sid'])
            _register_answer(user, form, text, msg)
    except rentals.BookingError as exc:
        store.delete_form(uid)
        _send(uid, str(exc))


def callback(cq, update_id):
    user = cq.get('from') or {}
    uid = user.get('id')
    parts = (cq.get('data') or '').split(':')
    message = cq.get('message') or {}
    if (not uid or len(parts) < 3 or message.get('chat', {}).get('type') != 'private'
            or message.get('chat', {}).get('id') != uid):
        telegram.answer_callback_query(cq['id'], 'Please use your private chat with the bot.')
        return
    try:
        telegram.answer_callback_query(cq['id'])
    except Exception:
        log.warning('Callback acknowledgement unavailable; continuing')
    nonce, action, arg = parts[1], parts[2], (parts[3] if len(parts) > 3 else '')
    if action == 'post':
        _post(uid, nonce)
        return
    form = store.get_form(uid)
    if not form or form['nonce'] != nonce:
        _send(uid, EXPIRED)
        return
    if update_id <= form.get('last_update', -1):
        _prompt(uid, form)  # Telegram retried after a failure: re-ask the current question.
        return
    form['last_update'] = update_id
    if action == 'cancel':
        store.delete_form(uid)
        _send(uid, 'Cancelled. Nothing was saved.')
        return
    if action == 'restart':
        form.update(step='title' if form['kind'] == 'setup' else 'name', data={})
        if _save(uid, form):
            _prompt(uid, form)
        return
    if form['kind'] != 'register':
        _send(uid, EXPIRED)
        return
    try:
        _event_session(form['sid'])
        advance = _register_button(user, form, action, arg, update_id)
    except rentals.BookingError as exc:
        store.delete_form(uid)
        _send(uid, str(exc))
        return
    except ef.FormError as exc:
        _send(uid, str(exc))
        return
    if advance and _save(uid, form):
        _prompt(uid, form)
