import importlib.util
import sys
import types
from copy import deepcopy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1] / 'functionapp'
SID = '01KSHRP2YPVJX1RBJ1DPJB8X8Z'
MSG = {'from': {'id': 123}, 'chat': {'id': 123, 'type': 'private'}}


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def setup(monkeypatch):
    state = {'session': {'id': SID, 'session_date': '2026-09-29', 'start_time': '18:30',
                        'end_time': '21:30', 'location': 'Test', 'poll_chat_id': -100456,
                        'poll_message_id': 77}, 'sent': [], 'deleted': [], 'frozen': [], 'polls': [], 'edits': []}
    shared = types.ModuleType('shared')
    shared.config = types.SimpleNamespace(admin_ids=lambda: {123})
    def mark(sid, uid):
        state['deleted'].append((sid, uid))
        state['session']['deleted'] = True
    shared.db = types.SimpleNamespace(
        get_session=lambda sid, **kw: state['session'] if sid == SID else None,
        get_responses=lambda sid: [{'telegram_id': 1, 'category': 'sit_student'},
                                   {'telegram_id': 1, 'category': 'rental_skates'}],
        list_sessions_for_admin=lambda: [state['session']], mark_session_deleted=mark)
    shared.booking_store = types.SimpleNamespace(close_deleted_session=lambda sid: state['frozen'].append(sid))
    shared.telegram = types.SimpleNamespace(
        send_message=lambda uid, text: state['sent'].append((uid, text)),
        delete_message=lambda *args: state['polls'].append(args) or True,
        edit_message_text=lambda *args, **kw: state['edits'].append((args, kw)))
    monkeypatch.setitem(sys.modules, 'shared', shared)
    return load('session_admin_test', ROOT / 'shared/session_admin.py'), state, shared


def test_preview_never_deletes(setup):
    admin, state, _ = setup
    admin.delete_session(MSG, [SID])
    assert not state['deleted'] and not state['frozen'] and not state['polls']
    text = state['sent'][-1][1]
    assert 'Attendees: 1; rentals: 1' in text
    assert f'/deletesession {SID} CONFIRM' in text
    assert 'CSV/Excel reports are not removed' in text


@pytest.mark.parametrize('args', [[], ['latest', 'CONFIRM'], [SID, 'yes'], [SID, 'CONFIRM', 'extra'], ["bad'id"]])
def test_invalid_delete_never_writes(setup, args):
    admin, state, _ = setup
    admin.delete_session(MSG, args)
    assert not state['deleted'] and not state['polls']


@pytest.mark.parametrize('msg', [
    {'from': {'id': 999}, 'chat': {'id': 999, 'type': 'private'}},
    {'from': {'id': 123}, 'chat': {'id': -100456, 'type': 'supergroup'}},
    {'from': {'id': 123}, 'chat': {'id': 999, 'type': 'private'}},
])
def test_delete_requires_admin_own_private_chat(setup, msg):
    admin, state, _ = setup
    admin.delete_session(msg, [SID, 'CONFIRM'])
    assert not state['deleted'] and not state['polls']


def test_confirm_targets_exact_session_and_original_poll(setup):
    admin, state, _ = setup
    admin.delete_session(MSG, [SID, 'CONFIRM'])
    assert state['deleted'] == [(SID, 123)]
    assert state['frozen'] == [SID]
    assert state['polls'] == [(-100456, 77)]
    assert 'retained internally' in state['sent'][-1][1]


def test_unknown_id_never_deletes(setup):
    admin, state, _ = setup
    admin.delete_session(MSG, ['01KSHRP2YPVJX1RBJ1DPJB8X8Y', 'CONFIRM'])
    assert not state['deleted'] and not state['polls']


def test_missing_original_chat_never_guesses_group(setup):
    admin, state, _ = setup
    del state['session']['poll_chat_id']
    admin.delete_session(MSG, [SID, 'CONFIRM'])
    assert not state['polls'] and not state['edits']
    assert 'not recorded' in state['sent'][-1][1]


def test_undeletable_poll_gets_closed_notice(setup):
    admin, state, shared = setup
    def fail(*args):
        raise RuntimeError('too old')
    shared.telegram.delete_message = fail
    admin.delete_session(MSG, [SID, 'CONFIRM'])
    assert state['edits'][0][1]['reply_markup'] == {'inline_keyboard': []}
    assert 'closed notice' in state['sent'][-1][1]


def test_cleanup_failure_reports_retry_without_losing_tombstone(setup):
    admin, state, shared = setup
    def fail(*args, **kw):
        raise RuntimeError('offline')
    shared.telegram.delete_message = fail
    shared.telegram.edit_message_text = fail
    admin.delete_session(MSG, [SID, 'CONFIRM'])
    assert state['session']['deleted'] is True
    assert 'Poll cleanup failed' in state['sent'][-1][1]


def test_partial_freeze_failure_can_be_retried(setup):
    admin, state, shared = setup
    def fail(sid):
        raise RuntimeError('storage offline')
    shared.booking_store.close_deleted_session = fail
    admin.delete_session(MSG, [SID, 'CONFIRM'])
    assert state['session']['deleted'] is True
    assert not state['polls']
    assert 'Retry this exact command' in state['sent'][-1][1]
    shared.booking_store.close_deleted_session = lambda sid: state['frozen'].append(sid)
    admin.delete_session(MSG, [SID, 'CONFIRM'])
    assert state['polls'] == [(-100456, 77)]


def test_list_and_pagination(setup):
    admin, state, shared = setup
    shared.db.list_sessions_for_admin = lambda: [deepcopy(state['session']) for _ in range(10)]
    admin.list_sessions(MSG, [])
    assert 'page 1/2' in state['sent'][-1][1]
    assert 'Next page: /sessions 2' in state['sent'][-1][1]
    admin.list_sessions(MSG, ['2'])
    assert 'page 2/2' in state['sent'][-1][1]


def test_deleted_sessions_hidden_from_all_normal_db_reads(monkeypatch):
    import azure.data.tables
    import azure.identity
    rows = [
        {'PartitionKey': 'SESSION', 'RowKey': 'live', 'session_date': '2099-01-01', 'created_at': 'a'},
        {'PartitionKey': 'SESSION', 'RowKey': 'removed', 'session_date': '2099-01-02', 'deleted': True},
    ]
    table = types.SimpleNamespace(query_entities=lambda query: rows,
        get_entity=lambda partition_key, row_key: next(row for row in rows if row['RowKey'] == row_key))
    monkeypatch.setattr(azure.data.tables, 'TableServiceClient', lambda **kw: types.SimpleNamespace(get_table_client=lambda name: table))
    monkeypatch.setattr(azure.identity, 'DefaultAzureCredential', lambda: None)
    shared = types.ModuleType('shared')
    shared.config = types.SimpleNamespace(COSMOS_ENDPOINT='https://test.invalid', TABLE_MEMBERS='members',
        TABLE_SESSIONS='sessions', TABLE_RESPONSES='responses', TIMEZONE='Asia/Singapore')
    monkeypatch.setitem(sys.modules, 'shared', shared)
    db = load('session_db_test', ROOT / 'shared/db.py')
    assert db.get_session('removed') is None
    assert db.get_session('removed', include_deleted=True)['deleted'] is True
    assert db.get_latest_session()['id'] == 'live'
    assert db.get_upcoming_session()['id'] == 'live'
    assert [s['id'] for s in db.list_unexported_sessions()] == ['live']
    assert [s['id'] for s in db.list_sessions_since('2000-01-01', include_skipped=True)] == ['live']
    assert [s['id'] for s in db.list_sessions_for_admin()] == ['live']
