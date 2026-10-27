"""Special events end to end: the real webhook, events, booking and exporter
code on moto's DynamoDB, SSM and EventBridge Scheduler; only Telegram is faked."""
from __future__ import annotations

import csv
import importlib
import importlib.util
import io
import json
import logging
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import boto3
import pytest

ROOT = Path(__file__).resolve().parents[1]
ADMIN, MEMBER, ALUM = 111, 500, 600
SOFTWARE_ENG = 'Bachelor of Engineering with Honours in Information and Communications Technology majoring in Software Engineering'
EVENT_DAY = (date.today() + timedelta(days=10)).isoformat()


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def env(ddb_tables, monkeypatch):
    monkeypatch.setenv('BOOKINGS_ENABLED', 'true')
    monkeypatch.setenv('BOT_USERNAME', 'SITSkateClubBot')
    monkeypatch.setenv('EXPORTER_LAMBDA_ARN', 'arn:aws:lambda:ap-southeast-1:123456789012:function:exp')
    monkeypatch.setenv('SCHEDULER_TARGET_ROLE', 'arn:aws:iam::123456789012:role/target')
    from shared import (
        booking_flow,
        booking_store,
        config,
        db,
        event_forms,
        events,
        export_schedule,
        poll,
        rentals,
        session_admin,
        telegram,
    )
    for module in (config, db, rentals, booking_store, poll, booking_flow, export_schedule,
                   event_forms, events, session_admin):
        importlib.reload(module)
    tg = {'sent': [], 'edits': [], 'deleted': [], 'answers': [], 'documents': []}

    def send_message(chat_id, text, reply_markup=None, message_thread_id=None):
        tg['sent'].append({'chat_id': chat_id, 'text': text, 'reply_markup': reply_markup,
                           'message_thread_id': message_thread_id})
        return {'message_id': 1000 + len(tg['sent'])}
    monkeypatch.setattr(telegram, 'send_message', send_message)
    monkeypatch.setattr(telegram, 'edit_message_text',
                        lambda *a, **kw: tg['edits'].append((a, kw)))
    monkeypatch.setattr(telegram, 'delete_message', lambda chat, mid: tg['deleted'].append((chat, mid)))
    monkeypatch.setattr(telegram, 'answer_callback_query', lambda *a, **kw: tg['answers'].append((a, kw)))
    monkeypatch.setattr(telegram, '_call', lambda *a, **kw: tg['answers'].append((a, kw)))
    monkeypatch.setattr(telegram, 'send_document',
                        lambda chat, filename, content, caption=None: tg['documents'].append((chat, filename, content)))
    webhook = _load('events_webhook_test', ROOT / 'services/webhook/handler.py')
    counter = {'update': 0, 'message': 0}

    def next_update():
        counter['update'] += 1
        return counter['update']

    def user(uid):
        return {'id': uid, 'first_name': f'User{uid}', 'username': f'user{uid}'}

    def dm(uid, text):
        counter['message'] += 1
        webhook._handle_message({'from': user(uid), 'chat': {'id': uid, 'type': 'private'},
                                 'text': text, 'message_id': counter['message']}, next_update())
        return counter['message']

    def press(uid, data):
        webhook._handle_callback({'id': 'cb', 'from': user(uid), 'data': data,
                                  'message': {'chat': {'id': uid, 'type': 'private'}}}, next_update())

    def buttons(index=-1):
        markup = tg['sent'][index]['reply_markup'] or {}
        return {b['text']: b.get('callback_data') or b.get('url')
                for row in markup.get('inline_keyboard', []) for b in row}

    def create_event(title='Halloween Skate'):
        dm(ADMIN, '/specialevent')
        for answer in (title, EVENT_DAY, '19:00', '22:00', 'SIT Punggol Campus Court',
                       'Costumes encouraged! Bring water.'):
            dm(ADMIN, answer)
        press(ADMIN, buttons()['Post to SIT announcements'])
        return [s for s in db.list_sessions_for_admin() if s.get('kind') == 'event'][0]

    def register_sit(uid=MEMBER, skates=False, sid=None):
        dm(uid, f'/start evs_{sid}')
        dm(uid, 'Tan Ah Kow')
        dm(uid, '2402201')
        press(uid, buttons()['ICT'])
        press(uid, buttons()['ICT (Software Engineering)'])
        press(uid, buttons()['Year 2'])
        press(uid, buttons()['Yes, I need skates' if skates else 'No'])
        press(uid, buttons()['Confirm registration'])

    def register_alumni(uid=ALUM, sid=None, masked='T567D'):
        dm(uid, f'/start eva_{sid}')
        dm(uid, 'Lim Mei Ling')
        dm(uid, masked)
        press(uid, buttons()['Confirm registration'])

    return types_ns(webhook=webhook, db=db, store=booking_store, tg=tg, dm=dm, press=press,
                    buttons=buttons, create_event=create_event, register_sit=register_sit,
                    register_alumni=register_alumni, events=events)


class types_ns(dict):
    __getattr__ = dict.__getitem__


def _state_items():
    return boto3.resource('dynamodb').Table('skatebot-test-state').scan()['Items']


# ── Admin setup ──────────────────────────────────────────────────────────────

def test_admin_creates_event_and_it_is_announced(env):
    session = env.create_event()
    assert session['group_key'] == 'event' and session['kind'] == 'event'
    assert session['title'] == 'Halloween Skate' and session['session_date'] == EVENT_DAY
    assert (session['start_time'], session['end_time']) == ('19:00', '22:00')
    assert session['details'] == 'Costumes encouraged! Bring water.'
    announcement = next(m for m in env.tg['sent'] if m['chat_id'] == -100999)
    assert announcement['message_thread_id'] == 3041
    assert 'Halloween Skate' in announcement['text'] and 'Costumes encouraged' in announcement['text']
    labels = [b['text'] for row in announcement['reply_markup']['inline_keyboard'] for b in row]
    assert labels == ['Register: SIT student', 'Register: Alumni / External']
    links = [b['url'] for row in announcement['reply_markup']['inline_keyboard'] for b in row]
    assert links == [f"https://t.me/SITSkateClubBot?start=evs_{session['id']}",
                     f"https://t.me/SITSkateClubBot?start=eva_{session['id']}"]
    assert session['poll_message_id'] == 1000 + env.tg['sent'].index(announcement) + 1
    schedules = boto3.client('scheduler').list_schedules()['Schedules']
    assert [s['Name'] for s in schedules] == [f"skatebot-prod-export-{session['id']}"]
    assert env.store.get_form(ADMIN) is None


def test_setup_rejects_bad_answers_without_advancing(env):
    env.dm(ADMIN, '/specialevent')
    env.dm(ADMIN, 'Glow Skate')
    for bad, error in (('31/10/2026', 'YYYY-MM-DD'), ('2020-01-01', 'past')):
        env.dm(ADMIN, bad)
        assert error in env.tg['sent'][-1]['text']
    env.dm(ADMIN, EVENT_DAY)
    env.dm(ADMIN, '7pm')
    assert 'HH:MM' in env.tg['sent'][-1]['text']
    env.dm(ADMIN, '19:00')
    env.dm(ADMIN, '18:00')
    assert 'after the start' in env.tg['sent'][-1]['text']
    assert env.store.get_form(ADMIN)['step'] == 'end'
    env.dm(ADMIN, '/cancel')
    assert env.store.get_form(ADMIN) is None
    assert not [s for s in env.db.list_sessions_for_admin() if s.get('kind') == 'event']


def test_non_admin_cannot_create_events(env):
    env.dm(MEMBER, '/specialevent')
    assert env.tg['sent'][-1]['text'] == '⛔ Admin only.'
    assert env.store.get_form(MEMBER) is None


def test_double_post_creates_one_event(env):
    env.dm(ADMIN, '/specialevent')
    for answer in ('Glow Skate', EVENT_DAY, '19:00', '22:00', 'Court', 'Details'):
        env.dm(ADMIN, answer)
    post = env.buttons()['Post to SIT announcements']
    env.press(ADMIN, post)
    env.press(ADMIN, post)
    assert len([s for s in env.db.list_sessions_for_admin() if s.get('kind') == 'event']) == 1


def test_event_never_suppresses_the_weekly_training_poll(env):
    today = datetime.now(ZoneInfo('Asia/Singapore')).date()
    env.db.create_session((today + timedelta(days=2)).isoformat(), '19:00', '22:00', 'Court',
                          group_key='event', group_name='Special event', kind='event', title='Glow')
    assert env.db.get_upcoming_session() is None
    assert env.db.get_latest_session() is None


# ── Registration ─────────────────────────────────────────────────────────────

def test_sit_registration_is_saved_and_announced_by_display_name(env):
    sid = env.create_event()['id']
    env.register_sit(sid=sid)
    person = env.store.read(sid)['people'][str(MEMBER)]
    assert person['attendance'] == 'sit_student' and person['current_student'] is True
    assert person['event_form'] == {'category': 'sit', 'name': 'Tan Ah Kow', 'student_no': '2402201',
                                    'cluster': 'ICT', 'programme': SOFTWARE_ENG,
                                    'year': 'Year 2', 'skates': False}
    assert 'rental' not in person
    announcement = env.tg['edits'][-1][0][2]
    assert 'SIT students: (1' in announcement and f'User{MEMBER}' in announcement
    assert '2402201' not in announcement and 'Tan Ah Kow' not in announcement
    assert env.store.get_form(MEMBER) is None
    assert 'You are registered' in env.tg['sent'][-1]['text']


def test_student_number_message_is_deleted_from_the_chat(env):
    sid = env.create_event()['id']
    env.dm(MEMBER, f'/start evs_{sid}')
    env.dm(MEMBER, 'Tan Ah Kow')
    mid = env.dm(MEMBER, '2402201')
    assert (MEMBER, mid) in env.tg['deleted']


def test_sit_registration_with_skates_reserves_a_pair(env):
    sid = env.create_event()['id']
    env.register_sit(sid=sid, skates=True)
    assert 'Choose your normal EU shoe size' in env.tg['sent'][-1]['text']
    env.press(MEMBER, env.buttons()['EU 40 · 9 exact / 9 larger'])
    env.press(MEMBER, env.buttons()['Continue'])
    env.press(MEMBER, env.buttons()['No — confirm rental'])
    person = env.store.read(sid)['people'][str(MEMBER)]
    assert person['rental'] == {'requested': 40, 'allocated': 40, 'guards': False}
    assert person['event_form']['student_no'] == '2402201'
    assert 'Rental skates: (1' in env.tg['edits'][-1][0][2]


def test_alumni_full_nric_is_rejected_deleted_and_never_stored(env):
    sid = env.create_event()['id']
    env.dm(ALUM, f'/start eva_{sid}')
    env.dm(ALUM, 'Lim Mei Ling')
    mid = env.dm(ALUM, 'S1234567D')
    assert (ALUM, mid) in env.tg['deleted']
    assert 'do NOT send your full NRIC' in env.tg['sent'][-1]['text']
    assert '1234567' not in json.dumps(_state_items(), default=str)
    env.dm(ALUM, ' t567d ')
    env.press(ALUM, env.buttons()['Confirm registration'])
    person = env.store.read(sid)['people'][str(ALUM)]
    assert person['attendance'] == 'non_sit' and person['current_student'] is False
    assert person['event_form'] == {'category': 'alumni', 'name': 'Lim Mei Ling', 'masked_id': 'T567D'}
    assert 'Alumni / External: (1' in env.tg['edits'][-1][0][2]


def test_rental_and_vote_paths_cannot_bypass_registration(env):
    sid = env.create_event()['id']
    env.dm(MEMBER, f'/start rent_{sid}')
    assert 'register for this event' in env.tg['sent'][-1]['text']
    env.webhook._handle_callback({'id': 'v', 'from': {'id': MEMBER}, 'data': f'vote:{sid}:sit_student',
                                  'message': {'chat': {'id': -100999}, 'message_id': 1}}, 999)
    assert str(MEMBER) not in (env.store.read(sid) or {'people': {}})['people']


def test_cancelling_registration_removes_details(env):
    sid = env.create_event()['id']
    env.register_sit(sid=sid)
    env.press(MEMBER, env.buttons()['Cancel registration'])
    person = env.store.read(sid)['people'][str(MEMBER)]
    assert person['attendance'] is None and 'event_form' not in person


def test_stale_and_expired_forms_are_rejected(env):
    sid = env.create_event()['id']
    env.dm(MEMBER, f'/start evs_{sid}')
    env.dm(MEMBER, 'Tan Ah Kow')
    env.dm(MEMBER, '2402201')
    old_cluster = env.buttons()['ICT']
    env.dm(MEMBER, '/cancel')
    env.dm(MEMBER, f'/start evs_{sid}')  # A fresh form gets a new nonce.
    env.press(MEMBER, old_cluster)
    assert 'expired or was replaced' in env.tg['sent'][-1]['text']
    assert env.store.get_form(MEMBER, now=time.time() + 31 * 60) is None
    # Found expired, the form is deleted at once (TTL can take up to 48h).
    assert not [i for i in _state_items() if i['pk'] == 'FORM']


def test_registration_details_never_reach_the_logs(env, caplog):
    # INFO is what the Lambdas emit (botocore's DEBUG request dumps are off).
    caplog.set_level(logging.INFO)
    sid = env.create_event()['id']
    env.register_sit(sid=sid)
    env.register_alumni(sid=sid)
    assert '2402201' not in caplog.text and 'T567D' not in caplog.text
    assert 'Tan Ah Kow' not in caplog.text and 'Lim Mei Ling' not in caplog.text


# ── Export, deletion and purge ───────────────────────────────────────────────

def test_export_sends_event_csv_then_deletes_details(env):
    sid = env.create_event()['id']
    env.register_sit(sid=sid)
    env.register_alumni(sid=sid)
    exporter = _load('events_exporter_test', ROOT / 'services/exporter/handler.py')
    result = exporter.lambda_handler({'session_id': sid}, None)
    assert result['status'] == 'exported' and result['delivered'] == 2
    chat, filename, content = env.tg['documents'][0]
    assert filename == f'{EVENT_DAY}_event_{sid}.csv'
    rows = list(csv.reader(io.StringIO(content.decode('utf-8-sig'))))
    assert rows[0][:8] == ['S/N', 'Full Name', 'Student ID', 'Cluster', 'Degree Programme',
                           'Year of Study', 'Type of Participation', 'Remarks']  # OSE form order
    assert rows[1][:8] == ['1', 'Tan Ah Kow', '2402201', 'ICT', SOFTWARE_ENG, 'Year 2', 'Member', '']
    assert rows[1][8] == 'SIT' and rows[1][14] == 'Halloween Skate'
    assert rows[2][:10] == ['2', 'Lim Mei Ling', '', '', '', '', 'Member', '', 'Alumni / External', 'T567D']
    people = env.store.read(sid)['people']
    assert all('event_form' not in p for p in people.values())
    assert people[str(MEMBER)]['attendance'] == 'sit_student'  # Names/attendance stay.
    dump = json.dumps(_state_items(), default=str)
    assert '2402201' not in dump and 'T567D' not in dump
    assert env.db.get_session(sid)['exported'] == 1


def test_deleting_an_event_deletes_details_immediately(env):
    sid = env.create_event()['id']
    env.register_alumni(sid=sid)
    env.dm(ADMIN, f'/deletesession {sid} CONFIRM')
    assert 'T567D' not in json.dumps(_state_items(), default=str)
    exporter = _load('events_exporter_deleted_test', ROOT / 'services/exporter/handler.py')
    assert exporter.lambda_handler({'session_id': sid}, None)['status'] == 'skipped'
    assert not env.tg['documents']


def test_events_listed_and_selected_by_name(env):
    sid = env.create_event()['id']
    env.dm(ADMIN, '/sessions Events')
    assert sid in env.tg['sent'][-1]['text']
    env.dm(ADMIN, '/summary Events')
    assert 'Halloween Skate' in env.tg['sent'][-1]['text']
    env.dm(ADMIN, '/sendpoll Events')
    assert '/specialevent' in env.tg['sent'][-1]['text']


# ── Course field and review fixes ────────────────────────────────────────────

def test_programme_back_and_not_listed(env):
    sid = env.create_event()['id']
    env.dm(MEMBER, f'/start evs_{sid}')
    env.dm(MEMBER, 'Tan Ah Kow')
    env.dm(MEMBER, '2402201')
    env.press(MEMBER, env.buttons()['ENG'])
    assert 'Robotics Systems' in env.buttons() and len(env.buttons()) == 22 + 2
    env.press(MEMBER, env.buttons()['‹ Back to clusters'])
    assert 'Which cluster' in env.tg['sent'][-1]['text']
    env.press(MEMBER, env.buttons()['HSS'])
    env.press(MEMBER, env.buttons()['Not listed'])
    env.press(MEMBER, env.buttons()['Other'])
    env.press(MEMBER, env.buttons()['No'])
    assert 'Programme: Programme not listed' in env.tg['sent'][-1]['text']
    env.press(MEMBER, env.buttons()['Confirm registration'])
    form = env.store.read(sid)['people'][str(MEMBER)]['event_form']
    assert (form['cluster'], form['programme'], form['year']) == ('HSS', None, 'Other')
    exporter = _load('events_exporter_notlisted', ROOT / 'services/exporter/handler.py')
    exporter.lambda_handler({'session_id': sid}, None)
    row = list(csv.reader(io.StringIO(env.tg['documents'][0][2].decode('utf-8-sig'))))[1]
    assert row[3:8] == ['HSS', '', 'Other', 'Member', 'Programme not listed']


def test_name_with_an_id_is_rejected_deleted_and_not_stored(env):
    sid = env.create_event()['id']
    env.dm(MEMBER, f'/start evs_{sid}')
    mid = env.dm(MEMBER, 'Tan S1234567D')
    assert (MEMBER, mid) in env.tg['deleted']
    assert 'cannot contain numbers' in env.tg['sent'][-1]['text']
    assert '1234567' not in json.dumps(_state_items(), default=str)


def test_repeated_registration_link_resumes_the_form(env):
    sid = env.create_event()['id']
    env.dm(MEMBER, f'/start evs_{sid}')
    env.dm(MEMBER, 'Tan Ah Kow')
    env.dm(MEMBER, f'/start evs_{sid}')
    assert 'student number' in env.tg['sent'][-1]['text']
    assert env.store.get_form(MEMBER)['data'] == {'name': 'Tan Ah Kow'}


def test_alumni_cannot_rent_or_switch_category(env):
    sid = env.create_event()['id']
    env.register_alumni(sid=sid)
    env.dm(ALUM, f'/start rent_{sid}')
    assert 'SIT students only' in env.tg['sent'][-1]['text']
    for action in ('student', 'menu', 'size:1:40'):
        env.press(ALUM, f'r:{sid}:{action}')
        assert 'rental' not in env.store.read(sid)['people'][str(ALUM)]
    person = env.store.read(sid)['people'][str(ALUM)]
    assert person['attendance'] == 'non_sit' and person['current_student'] is False


def test_failed_announcement_withdraws_the_event(env, monkeypatch):
    def fail(sid):
        raise RuntimeError('Telegram unavailable')
    monkeypatch.setattr(env.events, 'post_announcement', fail)
    env.dm(ADMIN, '/specialevent')
    for answer in ('Glow Skate', EVENT_DAY, '19:00', '22:00', 'Court', 'Details'):
        env.dm(ADMIN, answer)
    env.press(ADMIN, env.buttons()['Post to SIT announcements'])
    assert 'withdrawn' in env.tg['sent'][-1]['text']
    assert not [s for s in env.db.list_sessions_for_admin() if s.get('kind') == 'event']


def test_confirm_retry_after_a_failed_poll_refresh_completes(env, monkeypatch):
    from shared import telegram
    sid = env.create_event()['id']
    env.dm(MEMBER, f'/start evs_{sid}')
    env.dm(MEMBER, 'Tan Ah Kow')
    env.dm(MEMBER, '2402201')
    env.press(MEMBER, env.buttons()['ICT'])
    env.press(MEMBER, env.buttons()['ICT (Software Engineering)'])
    env.press(MEMBER, env.buttons()['Year 2'])
    env.press(MEMBER, env.buttons()['No'])
    confirm = {'id': 'cb', 'from': {'id': MEMBER, 'first_name': f'User{MEMBER}'},
               'data': env.buttons()['Confirm registration'],
               'message': {'chat': {'id': MEMBER, 'type': 'private'}}}
    real_edit = telegram.edit_message_text
    def flaky(*args, **kwargs):
        raise RuntimeError('Too Many Requests')
    monkeypatch.setattr(telegram, 'edit_message_text', flaky)
    with pytest.raises(RuntimeError):
        env.webhook._handle_callback(confirm, 4242)  # Webhook answers 503; Telegram retries.
    monkeypatch.setattr(telegram, 'edit_message_text', real_edit)
    env.webhook._handle_callback(confirm, 4242)
    assert 'You are registered' in env.tg['sent'][-1]['text']
    assert 'SIT students: (1' in env.tg['edits'][-1][0][2]
    assert env.store.get_form(MEMBER) is None


def test_skipping_an_event_deletes_details(env):
    sid = env.create_event()['id']
    env.register_alumni(sid=sid)
    env.dm(ADMIN, '/skipsession Events')
    assert env.db.get_session(sid)['skipped'] == 1
    assert 'T567D' not in json.dumps(_state_items(), default=str)


def test_scheduler_sweep_purges_details_left_behind(env, monkeypatch):
    sid = env.create_event()['id']
    env.register_alumni(sid=sid)
    later = env.create_event('Glow Skate')
    env.register_sit(sid=later['id'], uid=700)
    env.db.mark_session_exported(sid)  # Exported, but its purge never ran.
    scheduler = _load('events_scheduler_sweep', ROOT / 'services/scheduler/handler.py')
    scheduler.lambda_handler({}, None)
    assert 'T567D' not in json.dumps(_state_items(), default=str)
    # An upcoming, open event keeps its registrations.
    assert env.store.read(later['id'])['people']['700']['event_form']['student_no'] == '2402201'


def test_csv_cells_cannot_run_as_formulas(env):
    sid = env.create_event()['id']
    env.dm(ALUM, f'/start eva_{sid}')
    env.dm(ALUM, '=HYPERLINK("http://evil.example","click")')
    env.dm(ALUM, 'T567D')
    env.press(ALUM, env.buttons()['Confirm registration'])
    exporter = _load('events_exporter_formula', ROOT / 'services/exporter/handler.py')
    exporter.lambda_handler({'session_id': sid}, None)
    row = list(csv.reader(io.StringIO(env.tg['documents'][0][2].decode('utf-8-sig'))))[1]
    assert row[1].startswith("'=")
