"""Booking document concurrency and state-table records.

Two layers:
  - A thread-safe fake table that enforces the same two conditions the store
    uses, so real threads can race for the last pair. (moto's in-memory
    backend is not safe to race across threads.)
  - moto, to prove the real DynamoDB condition expressions behave as intended.
"""
import importlib.util
import json
import sys
import threading
import types
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path

import pytest
from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parents[1] / 'shared'


def load(name):
    spec = importlib.util.spec_from_file_location('storetest_' + name, ROOT / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _conditional_failure():
    return ClientError({'Error': {'Code': 'ConditionalCheckFailedException', 'Message': 'failed'}}, 'PutItem')


class Table:
    """Supports exactly the store's conditions: attribute_not_exists(pk) and #rev = :rev."""

    def __init__(self):
        self.rows = {}
        self.lock = threading.Lock()
        self.conflicts = 0

    def get_item(self, Key, ConsistentRead=False):
        with self.lock:
            item = self.rows.get((Key['pk'], Key['sk']))
            return {'Item': deepcopy(item)} if item else {}

    def put_item(self, Item, ConditionExpression=None, ExpressionAttributeNames=None,
                 ExpressionAttributeValues=None):
        with self.lock:
            key = Item['pk'], Item['sk']
            current = self.rows.get(key)
            if ConditionExpression == 'attribute_not_exists(pk)':
                if current is not None:
                    raise _conditional_failure()
            elif ConditionExpression == '#rev = :rev':
                if current is None or current['rev'] != ExpressionAttributeValues[':rev']:
                    self.conflicts += 1
                    raise _conditional_failure()
            elif ConditionExpression is not None:
                raise AssertionError(f'unexpected condition {ConditionExpression}')
            self.rows[key] = deepcopy(Item)


@pytest.fixture
def setup(monkeypatch):
    r = load('rentals')
    table = Table()
    shared = types.ModuleType('shared')
    shared.rentals = r
    shared.db = types.SimpleNamespace(_state=table, get_legacy_responses=lambda sid: [])
    monkeypatch.setitem(sys.modules, 'shared', shared)
    return load('booking_store'), r, table


def test_concurrent_last_pair_is_atomic(setup):
    store, r, table = setup
    initial = r.initial_state()
    initial['people'] = {str(uid): {'version': 0, 'attendance': None, 'current_student': True} for uid in (1, 2)}
    table.put_item({'pk': 'BOOKING', 'sk': 'test', 'rev': 0, 'payload': json.dumps(initial)})
    barrier = threading.Barrier(2)
    def book(uid):
        first = True
        def operation(state):
            nonlocal first
            if first:
                first = False
                barrier.wait(timeout=5)
            return r.apply_action(state, {'id': uid}, 'reserve', uid, requested=38, allocated=38)
        try:
            store.mutate('test', operation)
            return 'reserved'
        except r.BookingError:
            return 'unavailable'
    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(book, [1, 2]))
    assert sorted(result) == ['reserved', 'unavailable']
    assert table.conflicts == 1
    assert r.available(store.read('test'))[38] == 0


def test_concurrent_reservation_cannot_reopen_deleted_session(setup):
    store, r, _ = setup
    store.mutate('test', lambda state: r.apply_action(state, {'id': 1}, 'declare_student', 1))
    barrier = threading.Barrier(2)
    def run(delete):
        first = True
        def operation(state):
            nonlocal first
            if first:
                first = False
                barrier.wait(timeout=5)
            return {**state, 'deleted': True} if delete else r.apply_action(
                state, {'id': 1}, 'reserve', 2, requested=38, allocated=38)
        try:
            store.mutate('test', operation, allow_deleted=delete)
        except r.BookingError:
            assert not delete
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(run, [True, False]))
    assert store.read('test')['deleted'] is True


# ── Real DynamoDB semantics (moto) ───────────────────────────────────────────

@pytest.fixture
def rentals_module():
    from shared import rentals
    return rentals


def test_duplicate_mutation_is_idempotent(store_module, rentals_module):
    store, r = store_module, rentals_module
    def operation(state):
        return r.apply_action(state, {'id': 1}, 'sit_student', 1)
    assert store.mutate('test', operation) == store.mutate('test', operation)


def test_stale_revision_write_is_rejected_and_retried(store_module, rentals_module, db_module):
    store, r = store_module, rentals_module
    store.mutate('test', lambda state: r.apply_action(state, {'id': 1}, 'declare_student', 1))
    attempts = []
    def operation(state):
        attempts.append(1)
        if len(attempts) == 1:
            # Another writer commits after our read: our conditional write must fail.
            item = db_module._state.get_item(Key={'pk': 'BOOKING', 'sk': 'test'})['Item']
            db_module._state.put_item(Item={**item, 'rev': int(item['rev']) + 1})
        return r.apply_action(state, {'id': 1}, 'reserve', 2, requested=38, allocated=38)
    store.mutate('test', operation)
    assert len(attempts) == 2
    assert store.read('test')['people']['1']['rental']['allocated'] == 38


def test_oversize_state_does_not_overwrite(store_module, rentals_module):
    store, r = store_module, rentals_module
    store.mutate('test', lambda state: state)
    with pytest.raises(r.BookingError, match='storage is full'):
        store.mutate('test', lambda state: {**state, 'too_large': 'x' * 400_000})
    assert 'too_large' not in store.read('test')


def test_deleted_session_freezes_bookings_and_retains_data(store_module, rentals_module):
    store, r = store_module, rentals_module
    store.mutate('test', lambda state: r.apply_action(state, {'id': 1}, 'declare_student', 1))
    store.mutate('test', lambda state: r.apply_action(state, {'id': 1}, 'reserve', 2, requested=38, allocated=38))
    store.close_deleted_session('test')
    assert store.read('test')['people']['1']['rental']['allocated'] == 38
    with pytest.raises(r.BookingError, match='deleted'):
        store.mutate('test', lambda state: r.apply_action(state, {'id': 1}, 'cancel_rental', 3))
    store.close_deleted_session('test')  # Safe confirmation retry.
    assert store.read('test')['deleted'] is True


def test_profiles_merge_and_claims_are_first_come(store_module):
    store = store_module
    assert store.profile(5) == {}
    store.save_profile(5, member_username='tester')
    store.save_profile(5, current_student=True)
    assert store.profile(5) == {'member_username': 'tester', 'current_student': True}
    assert store.claim_member('tester', 5) is True
    assert store.claim_member('tester', 5) is True  # Same account: idempotent.
    assert store.claim_member('tester', 6) is False  # Recycled handle cannot take over.


def test_acknowledged_member_requires_unambiguous_acknowledgement(store_module, db_module):
    store, db = store_module, db_module
    db.update_member_fields('tester', {'full_name': 'T', 'rules_acknowledged': True, 'ambiguous': False})
    assert store.acknowledged_member({'id': 5, 'username': 'Tester'})['username'] == 'tester'
    assert store.profile(5)['member_username'] == 'tester'
    assert store.acknowledged_member({'id': 6, 'username': 'tester'}) is None
    db.update_member_fields('other', {'full_name': 'O', 'rules_acknowledged': True, 'ambiguous': True})
    assert store.acknowledged_member({'id': 7, 'username': 'other'}) is None
