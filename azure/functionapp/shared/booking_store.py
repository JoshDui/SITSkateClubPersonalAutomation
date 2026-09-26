"""Durable booking state in the existing sessions table, isolated by partition.

One compact document per session keeps stock and attendance in one atomic write.
No process-local locks: Azure can execute requests on multiple instances.
ETag API: learn.microsoft.com/python/api/azure-data-tables/azure.data.tables.tableclient
"""
from __future__ import annotations

import json
import time

from azure.core import MatchConditions
from azure.core.exceptions import ResourceExistsError, ResourceModifiedError, ResourceNotFoundError
from azure.data.tables import UpdateMode

from shared import db, rentals

PARTITION = 'BOOKING'


def read(session_id):
    try:
        entity = db._sessions.get_entity(partition_key=PARTITION, row_key=session_id)
        return json.loads(entity['payload'])
    except ResourceNotFoundError:
        return None


def mutate(session_id, operation, *, allow_deleted=False):
    for attempt in range(8):
        try:
            entity = db._sessions.get_entity(partition_key=PARTITION, row_key=session_id)
        except ResourceNotFoundError:
            state = rentals.initial_state(db.get_legacy_responses(session_id))
            try:
                db._sessions.create_entity({'PartitionKey': PARTITION, 'RowKey': session_id,
                                            'payload': json.dumps(state)})
            except ResourceExistsError:
                pass
            continue
        old_state = json.loads(entity['payload'])
        if old_state.get('deleted') and not allow_deleted:
            raise rentals.BookingError('This session has been deleted.')
        state = operation(old_state)
        if state == old_state:
            return state
        payload = json.dumps(state, ensure_ascii=True, separators=(',', ':'))
        # Table strings have a 64 KiB UTF-16 limit. Fail before writing, never truncate.
        if len(payload.encode('utf-16-le')) > 60000:
            raise rentals.BookingError('Session storage is full. Please contact an admin.')
        try:
            db._sessions.update_entity(
                {'PartitionKey': PARTITION, 'RowKey': session_id, 'payload': payload},
                mode=UpdateMode.REPLACE, etag=entity.metadata['etag'],
                match_condition=MatchConditions.IfNotModified,
            )
            return state
        except (ResourceModifiedError, ResourceExistsError):
            time.sleep(0.02 * (attempt + 1))
    raise rentals.BookingError('Bookings are busy. Please try again.')


def close_deleted_session(session_id):
    """Freeze retained bookings under the same ETag used by reservations.

    An in-flight reservation either commits before this marker or conflicts and
    sees the marker on retry. Retained rentals are history, not active bookings.
    """
    return mutate(session_id, lambda state: {**state, 'deleted': True}, allow_deleted=True)


def profile(user_id):
    try:
        return dict(db._members.get_entity(partition_key='TELEGRAM', row_key=str(user_id)))
    except ResourceNotFoundError:
        return {}


def save_profile(user_id, **fields):
    db._members.upsert_entity({'PartitionKey': 'TELEGRAM', 'RowKey': str(user_id), **fields},
                             mode=UpdateMode.MERGE)


def acknowledged_member(user):
    """Resolve a previously linked account first; never trust a typed username."""
    account = profile(user['id'])
    username = account.get('member_username') or user.get('username')
    if not username:
        return None
    member = db.get_member(username)
    if not member or not member.get('rules_acknowledged') or member.get('ambiguous'):
        return None
    # Claim the imported username once. A recycled handle cannot claim another identity.
    try:
        db._members.create_entity({'PartitionKey': 'CLAIM', 'RowKey': member['username'],
                                   'telegram_id': str(user['id'])})
    except ResourceExistsError:
        claim = db._members.get_entity(partition_key='CLAIM', row_key=member['username'])
        if claim['telegram_id'] != str(user['id']):
            return None
    if not account.get('member_username'):
        save_profile(user['id'], member_username=member['username'])
    return member
