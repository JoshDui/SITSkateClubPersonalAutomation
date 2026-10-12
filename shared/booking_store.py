"""Durable booking state in the state table, one document per session.

One compact document per session keeps stock and attendance in one atomic
write. No process-local locks: concurrent Lambda instances can handle taps for
the same session at once.

Optimistic concurrency: every document carries a `rev` counter and each write
is conditional on the revision that was read (DynamoDB ConditionExpression),
so two people cannot both take the last pair. This replaces the ETag
IfNotModified writes of the Azure Table version.
ConditionExpression docs: docs.aws.amazon.com/amazondynamodb/latest/developerguide/Expressions.ConditionExpressions.html
"""
from __future__ import annotations

import json
import time

from botocore.exceptions import ClientError

from shared import db, rentals

BOOKING = 'BOOKING'
PROFILE = 'TELEGRAM'
CLAIM = 'CLAIM'
FORM = 'FORM'

# In-progress forms (special-event setup and registration) expire after this.
# The same epoch is the table's TTL attribute, so abandoned forms, including
# any personal data typed so far, are also deleted by DynamoDB itself.
FORM_TTL_SECONDS = 30 * 60

# DynamoDB items are capped at 400 KB. Fail before writing, never truncate.
_MAX_PAYLOAD_BYTES = 350_000


def _conditional_failed(exc: ClientError) -> bool:
    return exc.response.get('Error', {}).get('Code') == 'ConditionalCheckFailedException'


def _get(pk, sk):
    return db._state.get_item(Key={'pk': pk, 'sk': sk}, ConsistentRead=True).get('Item')


def read(session_id):
    item = _get(BOOKING, session_id)
    return json.loads(item['payload']) if item else None


def mutate(session_id, operation, *, allow_deleted=False):
    for attempt in range(8):
        item = _get(BOOKING, session_id)
        if item is None:
            state = rentals.initial_state(db.get_legacy_responses(session_id))
            try:
                db._state.put_item(
                    Item={'pk': BOOKING, 'sk': session_id, 'rev': 0, 'payload': json.dumps(state)},
                    ConditionExpression='attribute_not_exists(pk)',
                )
            except ClientError as exc:
                if not _conditional_failed(exc):
                    raise
            continue
        old_state = json.loads(item['payload'])
        if old_state.get('deleted') and not allow_deleted:
            raise rentals.BookingError('This session has been deleted.')
        state = operation(old_state)
        if state == old_state:
            return state
        payload = json.dumps(state, ensure_ascii=True, separators=(',', ':'))
        if len(payload.encode('utf-8')) > _MAX_PAYLOAD_BYTES:
            raise rentals.BookingError('Session storage is full. Please contact an admin.')
        rev = int(item['rev'])
        try:
            db._state.put_item(
                Item={'pk': BOOKING, 'sk': session_id, 'rev': rev + 1, 'payload': payload},
                ConditionExpression='#rev = :rev',
                ExpressionAttributeNames={'#rev': 'rev'},
                ExpressionAttributeValues={':rev': rev},
            )
            return state
        except ClientError as exc:
            if not _conditional_failed(exc):
                raise
            time.sleep(0.02 * (attempt + 1))
    raise rentals.BookingError('Bookings are busy. Please try again.')


def close_deleted_session(session_id):
    """Freeze retained bookings under the same revision check used by reservations.

    An in-flight reservation either commits before this marker or conflicts and
    sees the marker on retry. Retained rentals are history, not active bookings.
    """
    return mutate(session_id, lambda state: {**state, 'deleted': True}, allow_deleted=True)


def purge_event_pii(session_id):
    """Remove special-event form answers once they are no longer needed
    (after the CSV export, or when the event is skipped or deleted)."""
    if read(session_id) is None:
        return None
    return mutate(session_id, rentals.strip_event_pii, allow_deleted=True)


# ── In-progress forms ────────────────────────────────────────────────────────

def get_form(user_id, now=None):
    """The user's live form, or None if missing or expired. An expired form is
    deleted on sight; DynamoDB's TTL can take up to two days to remove it."""
    item = _get(FORM, str(user_id))
    now = time.time() if now is None else now
    if not item:
        return None
    if int(item['expires_at']) <= now:
        delete_form(user_id)
        return None
    return {**json.loads(item['data']), 'rev': int(item['rev'])}


def save_form(user_id, form, now=None):
    """Store `form`, conditional on the revision it was read at (`form['rev']`,
    absent for a new form). Returns False if a newer write won the race."""
    now = time.time() if now is None else now
    data = {k: v for k, v in form.items() if k != 'rev'}
    rev = form.get('rev')
    item = {'pk': FORM, 'sk': str(user_id), 'data': json.dumps(data, separators=(',', ':')),
            'rev': 0 if rev is None else rev + 1, 'expires_at': int(now) + FORM_TTL_SECONDS}
    params = {'Item': item}
    if rev is not None:
        params.update(ConditionExpression='#rev = :rev', ExpressionAttributeNames={'#rev': 'rev'},
                      ExpressionAttributeValues={':rev': rev})
    try:
        db._state.put_item(**params)
        return True
    except ClientError as exc:
        if not _conditional_failed(exc):
            raise
        return False


def take_form(user_id, now=None):
    """Atomically remove and return the live form, so only one of two
    simultaneous confirmations acts on it."""
    resp = db._state.delete_item(Key={'pk': FORM, 'sk': str(user_id)}, ReturnValues='ALL_OLD')
    item = resp.get('Attributes')
    now = time.time() if now is None else now
    if not item or int(item['expires_at']) <= now:
        return None
    return json.loads(item['data'])


def delete_form(user_id):
    db._state.delete_item(Key={'pk': FORM, 'sk': str(user_id)})


def profile(user_id):
    item = _get(PROFILE, str(user_id))
    if not item:
        return {}
    return {k: v for k, v in db._to_dict(item).items() if k not in ('pk', 'sk')}


def save_profile(user_id, **fields):
    db._set_fields(db._state, {'pk': PROFILE, 'sk': str(user_id)}, fields)


def claim_member(username, telegram_id):
    """Bind an imported registry handle to one Telegram account, first come
    first served. Returns False if another account already holds the claim, so
    a recycled handle cannot take over someone else's registry entry."""
    try:
        db._state.put_item(
            Item={'pk': CLAIM, 'sk': username, 'telegram_id': str(telegram_id)},
            ConditionExpression='attribute_not_exists(pk)',
        )
        return True
    except ClientError as exc:
        if not _conditional_failed(exc):
            raise
    claim = _get(CLAIM, username)
    return bool(claim) and claim['telegram_id'] == str(telegram_id)


def acknowledged_member(user):
    """Resolve a previously linked account first; never trust a typed username."""
    account = profile(user['id'])
    username = account.get('member_username') or user.get('username')
    if not username:
        return None
    member = db.get_member(username)
    if not member or not member.get('rules_acknowledged') or member.get('ambiguous'):
        return None
    if not claim_member(member['username'], user['id']):
        return None
    if not account.get('member_username'):
        save_profile(user['id'], member_username=member['username'])
    return member
