import importlib.util
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1] / 'functionapp/shared'
SID = '01KSHRP2YPVJX1RBJ1DPJB8X8Z'
USER = {'id': 42, 'username': 'tester', 'first_name': 'Test'}


def load(name):
    spec = importlib.util.spec_from_file_location('flowtest_' + name, ROOT / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def setup(monkeypatch):
    r = load('rentals')
    shared = types.ModuleType('shared')
    shared.rentals = r
    state = {'value': r.initial_state(), 'ack': True, 'profile': {'current_student': True}, 'sent': [], 'edits': [], 'answers': []}
    state['value']['people']['42'] = {'version': 0, 'attendance': None, 'current_student': True}
    def mutate(sid, operation):
        state['value'] = operation(state['value'])
        return state['value']
    shared.booking_store = types.SimpleNamespace(
        read=lambda sid: state['value'], mutate=mutate,
        profile=lambda uid: state['profile'], save_profile=lambda uid, **kw: state['profile'].update(kw),
        acknowledged_member=lambda user: {'username': 'tester'} if state['ack'] else None)
    session = {'id': SID, 'session_date': '2099-01-01', 'start_time': '18:30', 'end_time': '21:30',
               'location': 'Test', 'poll_chat_id': -100123, 'poll_message_id': 77}
    shared.db = types.SimpleNamespace(get_session=lambda sid: session,
        get_legacy_responses=lambda sid: [], get_responses=lambda sid: r.responses(state['value'], sid))
    shared.config = types.SimpleNamespace(TIMEZONE='Asia/Singapore', RULES_FORM_URL='https://forms.example.test/rules',
        BOT_USERNAME='test_bot', BOOKINGS_ENABLED=True, attendance_poll_chat_id=lambda: -100123)
    shared.telegram = types.SimpleNamespace(
        send_message=lambda uid, text, **kw: state['sent'].append((uid, text, kw)),
        answer_callback_query=lambda *args, **kw: state['answers'].append((args, kw)),
        edit_message_text=lambda *args, **kw: state['edits'].append((args, kw)),
        _call=lambda *args, **kw: state['answers'].append((args, kw)))
    monkeypatch.setitem(sys.modules, 'shared', shared)
    shared.poll = load('poll')
    flow = load('booking_flow')
    def click(action, update=1):
        flow.callback({'id': 'callback', 'from': USER, 'data': f'r:{SID}:{action}',
                       'message': {'chat': {'type': 'private', 'id': USER['id']}}}, update)
    return flow, state, click, shared


def test_opening_dm_does_not_mark_attendance(setup):
    flow, state, _, _ = setup
    flow.start(USER, SID)
    assert not state['value']['people']['42'].get('attendance')
    assert 'Choose your normal EU' in state['sent'][-1][1]


def test_rules_missing_blocks_booking(setup):
    _, state, click, _ = setup
    state['ack'] = False
    click('book:0:38:38:1')
    assert not state['value']['people']['42'].get('attendance')
    assert 'rules acknowledgement' in state['sent'][-1][1]


def test_undeclared_student_cannot_book(setup):
    _, state, click, _ = setup
    state['profile'] = {}
    state['value']['people']['42']['current_student'] = False
    click('book:0:38:38:1')
    assert not state['value']['people']['42'].get('attendance')
    assert 'currently enrolled' in state['sent'][-1][1]


def test_confirm_updates_attendance_and_poll(setup):
    _, state, click, _ = setup
    click('book:0:38:38:1')
    assert state['value']['people']['42']['attendance'] == 'sit_student'
    assert state['value']['people']['42']['rental']['guards'] is True
    assert len(state['edits']) == 1
    click('book:0:38:38:1')
    assert state['value']['people']['42']['version'] == 1


def test_external_declaration_retains_attendance(setup):
    _, state, click, _ = setup
    click('book:0:38:38:0')
    click('external', 2)
    assert state['value']['people']['42']['attendance'] == 'non_sit'
    assert 'rental' not in state['value']['people']['42']
    assert state['value']['people']['42']['current_student'] is False


def test_larger_pair_requires_consent_button(setup):
    _, state, click, _ = setup
    click('size:0:36')
    assert 'Would you accept EU 37' in state['sent'][-1][1]
    assert not state['value']['people']['42'].get('attendance')


def test_closed_session_rejects_bookings(setup):
    _, state, click, shared = setup
    shared.db.get_session(SID)['session_date'] = '2000-01-01'
    click('book:0:38:38:0')
    assert not state['value']['people']['42'].get('attendance')
    assert 'close when training starts' in state['sent'][-1][1]


def test_group_attendance_requires_acknowledgement(setup):
    flow, state, _, _ = setup
    state['ack'] = False
    flow.attendance({'id': 'callback', 'from': USER, 'data': f'vote:{SID}:non_sit',
                     'message': {'chat': {'id': -100123}, 'message_id': 77}}, 1)
    assert not state['value']['people']['42'].get('attendance')
    assert 'rules_' in state['answers'][-1][1]['url']


def test_copied_group_poll_is_rejected(setup):
    flow, state, _, _ = setup
    flow.attendance({'id': 'callback', 'from': USER, 'data': f'vote:{SID}:sit_student',
                     'message': {'chat': {'id': -100123}, 'message_id': 78}}, 1)
    assert not state['value']['people']['42'].get('attendance')
    assert 'original group poll' in state['answers'][-1][0][1]


def test_poll_edit_failure_can_be_retried_without_duplicate_booking(setup):
    _, state, click, shared = setup
    def fail(*args, **kwargs):
        raise RuntimeError('temporary outage')
    shared.telegram.edit_message_text = fail
    with pytest.raises(RuntimeError):
        click('book:0:38:38:1')
    assert state['value']['people']['42']['version'] == 1
    shared.telegram.edit_message_text = lambda *args: state['edits'].append(args)
    click('book:0:38:38:1')
    assert state['value']['people']['42']['version'] == 1
    assert len(state['edits']) == 1


def test_large_poll_stays_within_telegram_limit(setup):
    _, _, _, shared = setup
    responses = [{'category': category, 'telegram_id': uid, 'first_name': '\U0001f600' * 80}
                 for category in shared.poll.CATEGORY_KEYS for uid in range(100)]
    text = shared.poll.build_poll_text(shared.db.get_session(SID), responses)
    assert len(text.encode('utf-16-le')) // 2 < 4096
    assert '100 people responded' in text


def test_expired_callback_does_not_retry_completed_attendance(setup):
    flow, state, _, shared = setup
    def expired(*args, **kwargs):
        raise RuntimeError('query is too old')
    shared.telegram.answer_callback_query = expired
    flow.attendance({'id': 'callback', 'from': USER, 'data': f'vote:{SID}:sit_student',
                     'message': {'chat': {'id': -100123}, 'message_id': 77}}, 1)
    assert state['value']['people']['42']['attendance'] == 'sit_student'


def test_declaration_replay_does_not_change_newer_session_status(setup):
    _, state, click, _ = setup
    click('external', 2)
    click('student', 3)
    click('external', 2)
    assert state['value']['people']['42']['current_student'] is True


def test_deletion_during_poll_refresh_does_not_restore_buttons(setup):
    flow, state, _, shared = setup
    original = shared.db.get_session(SID)
    reads = iter([original, None])
    shared.db.get_session = lambda sid: next(reads)
    flow.refresh_poll(SID)
    assert len(state['edits']) == 2
    assert state['edits'][-1][1]['reply_markup'] == {'inline_keyboard': []}


def test_missing_poll_reference_reports_saved_booking_without_false_success(setup):
    _, state, click, shared = setup
    shared.db.get_session(SID).pop('poll_message_id')
    click('book:0:38:38:1')
    assert state['value']['people']['42']['rental']['allocated'] == 38
    assert 'selection was saved' in state['sent'][-1][1]
    assert 'message reference is missing' in state['sent'][-1][1]
    assert not state['edits']
