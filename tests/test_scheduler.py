"""Weekly scheduler Lambda end to end: real shared modules on moto's DynamoDB,
SSM and EventBridge Scheduler; only Telegram is faked."""
from __future__ import annotations

import importlib
import importlib.util
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import boto3
import pytest

SCHEDULER = Path(__file__).resolve().parents[1] / 'services/scheduler/handler.py'
EXPORTER_ARN = 'arn:aws:lambda:ap-southeast-1:123456789012:function:skatebot-test-exporter'
ROLE_ARN = 'arn:aws:iam::123456789012:role/skatebot-test-scheduler-target-role'


@pytest.fixture
def env(ddb_tables, monkeypatch):
    monkeypatch.setenv('EXPORTER_LAMBDA_ARN', EXPORTER_ARN)
    monkeypatch.setenv('SCHEDULER_TARGET_ROLE', ROLE_ARN)
    monkeypatch.setenv('SCHEDULE_NAME_PREFIX', 'skatebot-test-export-')
    monkeypatch.setenv('BOOKINGS_ENABLED', 'true')
    monkeypatch.setenv('BOT_USERNAME', 'SITSkateClubBot')
    from shared import booking_store, config, db, export_schedule, poll, telegram
    for module in (config, db, booking_store, poll, export_schedule):
        importlib.reload(module)
    sent = []
    def send_message(chat_id, text, reply_markup=None, message_thread_id=None):
        sent.append({'chat_id': chat_id, 'text': text, 'reply_markup': reply_markup,
                     'message_thread_id': message_thread_id})
        return {'message_id': 900 + len(sent)}
    monkeypatch.setattr(telegram, 'send_message', send_message)
    spec = importlib.util.spec_from_file_location('scheduler_handler_test', SCHEDULER)
    handler = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(handler)
    return handler, db, sent


def _schedules():
    client = boto3.client('scheduler')
    return [client.get_schedule(Name=s['Name'], GroupName='default')
            for s in client.list_schedules()['Schedules']]


def test_weekly_run_creates_tuesday_session_posts_poll_and_schedules_export(env):
    handler, db, sent = env
    result = handler.lambda_handler({}, None)
    assert result['status'] == 'created'

    session = db.get_session(result['session_id'])
    assert datetime.fromisoformat(session['session_date']).weekday() == 1  # Tuesday
    assert session['group_key'] == 'sit'
    assert session['poll_message_id'] == 901
    assert session['poll_chat_id'] == -100999

    assert len(sent) == 1
    assert sent[0]['chat_id'] == -100999 and sent[0]['message_thread_id'] == 3041
    buttons = [b for row in sent[0]['reply_markup']['inline_keyboard'] for b in row]
    assert any(f"start=rent_{result['session_id']}" in b.get('url', '') for b in buttons)

    [schedule] = _schedules()
    assert schedule['Name'] == f"skatebot-test-export-{result['session_id']}"
    assert schedule['ScheduleExpressionTimezone'] == 'Asia/Singapore'
    fire = datetime.fromisoformat(schedule['ScheduleExpression'][3:-1])
    assert fire.date().isoformat() > session['session_date']  # 24h after the 18:30 start
    assert fire.strftime('%H:%M') == '18:30'
    assert schedule['Target']['Arn'] == EXPORTER_ARN
    assert json.loads(schedule['Target']['Input']) == {'session_id': result['session_id']}


def test_second_run_in_the_same_week_is_a_noop(env):
    handler, _, sent = env
    first = handler.lambda_handler({}, None)
    second = handler.lambda_handler({}, None)
    assert second == {'status': 'noop', 'session_id': first['session_id']}
    assert len(sent) == 1
    assert len(_schedules()) == 1


def test_manual_eight_wheel_session_does_not_suppress_sit_poll(env):
    handler, db, sent = env
    today = datetime.now(ZoneInfo('Asia/Singapore')).date().isoformat()
    db.create_session(today, '18:30', '21:30', 'SIT', group_key='8wheel', group_name='8 Wheel Clan',
                      poll_chat_id=-1002958151210)
    assert handler.lambda_handler({}, None)['status'] == 'created'
    assert len(sent) == 1


def test_skipped_week_is_honoured_but_deleted_session_is_replaced(env):
    handler, db, sent = env
    first = handler.lambda_handler({}, None)
    db.mark_session_skipped(first['session_id'])
    assert handler.lambda_handler({}, None)['status'] == 'noop'
    db.mark_session_deleted(first['session_id'], 123)
    assert handler.lambda_handler({}, None)['status'] == 'created'
    assert len(sent) == 2


def test_rescheduling_updates_the_existing_export_schedule(env):
    handler, db, _ = env
    sid = handler.lambda_handler({}, None)['session_id']
    from shared import export_schedule
    db.update_session_details(sid, '2099-03-03', '19:00', '22:00', 'Elsewhere')
    export_schedule.schedule_export(sid)
    [schedule] = _schedules()
    assert schedule['ScheduleExpression'] == 'at(2099-03-04T19:00:00)'


def test_past_session_export_fires_soon_instead_of_failing(env):
    _, db, _ = env
    from shared import export_schedule
    sid = db.create_session('2020-01-07', '18:30', '21:30', 'SIT')
    export_schedule.schedule_export(sid)
    [schedule] = _schedules()
    fire = datetime.fromisoformat(schedule['ScheduleExpression'][3:-1]).replace(tzinfo=ZoneInfo('Asia/Singapore'))
    assert 0 < (fire - datetime.now(ZoneInfo('Asia/Singapore'))).total_seconds() <= 120


def test_retry_after_failed_poll_resumes_instead_of_skipping_the_week(env, monkeypatch):
    handler, db, sent = env
    from shared import telegram
    real_send = telegram.send_message
    def failing(*args, **kwargs):
        raise RuntimeError('Telegram unavailable')
    monkeypatch.setattr(telegram, 'send_message', failing)
    with pytest.raises(RuntimeError):
        handler.lambda_handler({}, None)  # Session created, poll failed.
    monkeypatch.setattr(telegram, 'send_message', real_send)
    result = handler.lambda_handler({}, None)  # Lambda's async retry.
    assert result['status'] == 'resumed'
    assert db.get_session(result['session_id'])['poll_message_id'] == 901
    assert len(sent) == 1 and len(_schedules()) == 1
    assert handler.lambda_handler({}, None)['status'] == 'noop'
