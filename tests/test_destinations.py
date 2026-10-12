import importlib.util
import sys
import types
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from tests.test_webhook_callback import _load_webhook

ROOT = Path(__file__).resolve().parents[1]
SID = '01KSHRP2YPVJX1RBJ1DPJB8X8Z'
MSG = {'from': {'id': 123}, 'chat': {'id': 123, 'type': 'private'}}


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def setup(monkeypatch):
    webhook, _ = _load_webhook(monkeypatch)
    webhook.config.EIGHT_WHEEL_CHAT_ID = '-1002958151210'
    destinations = load('destination_test', ROOT / 'shared/destinations.py')
    monkeypatch.setattr(sys.modules['shared'], 'destinations', destinations, raising=False)
    state = {'sessions': {}, 'sent': [], 'exports': [], 'refs': []}
    def create(**kwargs):
        state['sessions'][SID] = {'id': SID, **kwargs, 'poll_topic_id': kwargs.get('poll_topic_id') or 0}
        return SID
    webhook.db.create_session = create
    webhook.db.get_session = lambda sid: state['sessions'].get(sid)
    webhook.db.set_poll_message_id = lambda *args: state['refs'].append(args)
    webhook.telegram.send_message = lambda *args, **kwargs: state['sent'].append((args, kwargs)) or {'message_id': 55}
    webhook.export_schedule.schedule_export = lambda sid: state['exports'].append(sid)
    return webhook, destinations, state


@pytest.mark.parametrize('name,expected', [('', 'sit'), ('SIT', 'sit'), ('8 Wheel Clan', '8wheel'),
                                           ('  8  wHeEl CLAN ', '8wheel'), ('8wheel', '8wheel')])
def test_destination_names(setup, name, expected):
    assert setup[1].key(name) == expected


@pytest.mark.parametrize('group,now,expected', [
    ('sit', '2026-09-27T10:00', '2026-09-29'),
    ('8wheel', '2026-09-27T10:00', '2026-10-01'),
    ('8wheel', '2026-10-01T18:29', '2026-10-01'),
    ('8wheel', '2026-10-01T18:30', '2026-10-08'),
    ('sit', '2026-09-29T18:30', '2026-10-06'),
])
def test_next_event_date(setup, group, now, expected):
    assert setup[1].next_date(group, datetime.fromisoformat(now).replace(tzinfo=ZoneInfo('Asia/Singapore'))) == expected


@pytest.mark.parametrize('args,chat,topic,group,weekday', [
    ([], -100222, 99, 'sit', 1),
    (['8', 'Wheel', 'Clan'], -1002958151210, None, '8wheel', 3),
])
def test_sendpoll_posts_only_selected_destination(setup, args, chat, topic, group, weekday):
    webhook, _, state = setup
    webhook._cmd_sendpoll(MSG, args)
    session = state['sessions'][SID]
    assert session['group_key'] == group
    assert datetime.fromisoformat(session['session_date']).weekday() == weekday
    group_posts = [kw for _, kw in state['sent'] if 'reply_markup' in kw]
    assert len(group_posts) == 1
    assert group_posts[0]['chat_id'] == chat
    assert group_posts[0]['message_thread_id'] == topic
    assert state['refs'] == [(SID, 55, chat)]
    assert state['exports'] == [SID]


@pytest.mark.parametrize('destination', ['typo', '8 Wheel Clam', '-100123'])
def test_unknown_destination_never_creates_or_posts(setup, destination):
    webhook, _, state = setup
    webhook._cmd_sendpoll(MSG, destination.split())
    assert not state['sessions'] and not state['refs'] and not state['exports']
    assert 'Unknown group' in state['sent'][0][0][1]


@pytest.mark.parametrize('configured', ['', '-100123', '-100222', 'bad', '123'])
def test_unconfigured_destination_never_falls_back_to_sit(setup, configured):
    webhook, _, state = setup
    webhook.config.EIGHT_WHEEL_CHAT_ID = configured
    webhook._cmd_sendpoll(MSG, ['8wheel'])
    assert not state['sessions'] and not state['refs']


def test_named_admin_selectors_and_invalid_selector(setup):
    webhook, _, state = setup
    selected = []
    webhook.db.get_latest_session = lambda key: selected.append(key) or {'id': SID}
    assert webhook._selected_session(MSG, [])['id'] == SID
    assert webhook._selected_session(MSG, ['8', 'Wheel', 'Clan'])['id'] == SID
    assert webhook._selected_session(MSG, ['typo']) is None
    assert selected == ['sit', '8wheel']


def test_nonadmin_cannot_select_destination(setup):
    webhook, _, state = setup
    webhook._handle_message({'from': {'id': 999}, 'chat': {'id': 999, 'type': 'private'},
                             'text': '/sendpoll 8 Wheel Clan'})
    assert not state['sessions']


def test_explicit_general_snapshot_does_not_inherit_sit_topic(setup):
    webhook, _, state = setup
    state['sessions'][SID] = {'id': SID, 'poll_chat_id': -1002958151210, 'poll_topic_id': 0}
    webhook._send_poll(SID)
    assert state['sent'][0][1]['message_thread_id'] is None
    assert state['refs'][0][2] == -1002958151210


def test_sets_and_skips_only_selected_groups(setup):
    webhook, _, state = setup
    chosen, edits, skipped = [], [], []
    def latest(key):
        chosen.append(key)
        return {'id': key, 'session_date': '2026-10-01'}
    webhook.db.get_latest_session = latest
    webhook.db.update_session_details = lambda *args: edits.append(args)
    webhook.db.mark_session_skipped = lambda sid: skipped.append(sid)
    webhook._cmd_setsession(MSG, ['2026-10-01', '18:30', '21:30', 'SIT', '--group', '8', 'Wheel', 'Clan'])
    assert edits == [('8wheel', '2026-10-01', '18:30', '21:30', 'SIT')]
    assert state['exports'] == ['8wheel']
    webhook._cmd_skipsession(MSG, [])
    webhook._cmd_skipsession(MSG, ['8wheel'])
    assert skipped == ['sit', '8wheel']
    webhook._cmd_setsession(MSG, ['2026-10-01', '18:30', '21:30', 'SIT', '--group', 'typo'])
    assert len(edits) == 1 and chosen == ['8wheel', 'sit', '8wheel']


def test_sessions_list_filters_group_and_paginates(setup, monkeypatch):
    webhook, destinations, state = setup
    monkeypatch.setattr(sys.modules['shared'], 'booking_store', types.SimpleNamespace(), raising=False)
    admin = load('destination_session_admin', ROOT / 'shared/session_admin.py')
    sit = {'id': 'sit', 'session_date': '2026-09-29', 'start_time': '18:30', 'end_time': '21:30'}
    thursday = {**sit, 'id': 'thu', 'group_key': '8wheel'}
    webhook.db.list_sessions_for_admin = lambda: [sit] + [thursday] * 9
    admin.list_sessions(MSG, [])
    assert '\nsit\n' in state['sent'][-1][0][1] and '\nthu\n' not in state['sent'][-1][0][1]
    admin.list_sessions(MSG, ['8', 'Wheel', 'Clan'])
    assert 'Next page: /sessions 8 Wheel Clan 2' in state['sent'][-1][0][1]
    admin.list_sessions(MSG, ['8', 'Wheel', 'Clan', '2'])
    assert 'page 2/2' in state['sent'][-1][0][1] and '\nsit\n' not in state['sent'][-1][0][1]
    admin.list_sessions(MSG, ['8', 'Wheel', 'Clan', '999'])
    assert '/sessions 8 Wheel Clan 2' in state['sent'][-1][0][1]


def test_db_group_isolation_and_route_serialization(db_module, monkeypatch):
    db = db_module
    soon = datetime.now(ZoneInfo('Asia/Singapore')).date().isoformat()
    # A session row written before group keys existed counts as SIT.
    db._sessions.put_item(Item={'id': 'legacy', 'gsi1_pk': 'SESSION', 'session_date': soon})
    db._sessions.put_item(Item={'id': 'thursday', 'gsi1_pk': 'SESSION', 'session_date': soon,
                                'group_key': '8wheel'})
    assert db.get_latest_session()['id'] == 'legacy'
    assert db.get_latest_session('8wheel')['id'] == 'thursday'
    assert db.get_upcoming_session()['id'] == 'legacy'
    db._sessions.delete_item(Key={'id': 'legacy'})
    assert db.get_upcoming_session() is None  # Thursday cannot suppress SIT scheduler.
    assert db.get_latest_session() is None
    monkeypatch.setattr(db, '_new_ulid', lambda: SID)
    db.create_session('2099-02-05', '18:30', '21:30', 'SIT', group_key='8wheel',
        group_name='8 Wheel Clan', poll_chat_id=-1002958151210, poll_topic_id=None)
    saved = db.get_session(SID)
    assert saved['poll_chat_id'] == -1002958151210 and type(saved['poll_chat_id']) is int
    assert saved['poll_topic_id'] == 0 and saved['group_key'] == '8wheel'
