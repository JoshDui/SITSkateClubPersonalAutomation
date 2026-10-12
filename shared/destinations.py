"""Explicit, allowlisted poll destinations; names never resolve arbitrary chats."""
from datetime import datetime, timedelta

from shared import config


def key(name=''):
    normalized = ' '.join(name.strip().strip('"').casefold().split())
    if normalized in ('', 'sit', 'sit inline skate'):
        return 'sit'
    if normalized in ('8 wheel clan', '8wheel', '8 wheel clan 八轮族'):
        return '8wheel'
    if normalized in ('event', 'events', 'special event', 'special events'):
        return 'event'
    raise ValueError('Unknown group. Use SIT, 8 Wheel Clan or Events. Nothing was sent or changed.')


def label(group_key):
    return {'sit': 'SIT Inline Skate', '8wheel': '8 Wheel Clan', 'event': 'Special events'}[group_key]


def route(group_key):
    if group_key == 'event':
        raise ValueError('Use /specialevent to create a special event. Nothing was sent.')
    if group_key == 'sit':
        return config.attendance_poll_chat_id(), config.attendance_poll_topic_id()
    if group_key != '8wheel':
        raise ValueError('Unknown group.')
    raw = getattr(config, 'EIGHT_WHEEL_CHAT_ID', '')
    if not raw:
        raise ValueError('8 Wheel Clan is not configured yet. Nothing was sent.')
    chat_id = int(raw)
    if chat_id != -1002958151210 or chat_id == config.attendance_poll_chat_id():
        raise ValueError('Invalid 8 Wheel Clan destination. Nothing was sent.')
    # General is represented by omitting message_thread_id, not SIT's topic.
    # API: core.telegram.org/bots/api#sendmessage (optional message_thread_id).
    return chat_id, None


def next_date(group_key, now):
    weekday = {'sit': 1, '8wheel': 3}[group_key]
    day = now.date() + timedelta(days=(weekday - now.weekday()) % 7)
    start = datetime.combine(day, datetime.strptime(config.DEFAULT_SESSION_START, '%H:%M').time(), tzinfo=now.tzinfo)
    if start <= now:
        day += timedelta(days=7)
    return day.isoformat()
