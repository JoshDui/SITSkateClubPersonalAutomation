import importlib.util
import json
import sys
import threading
import types
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path

from azure.core.exceptions import ResourceExistsError, ResourceModifiedError, ResourceNotFoundError
from azure.data.tables import TableEntity
import pytest

ROOT = Path(__file__).resolve().parents[1] / 'functionapp/shared'


def load(name):
    spec = importlib.util.spec_from_file_location('storetest_' + name, ROOT / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Table:
    def __init__(self):
        self.rows = {}
        self.lock = threading.Lock()
        self.conflicts = 0

    def get_entity(self, partition_key, row_key):
        with self.lock:
            if (partition_key, row_key) not in self.rows:
                raise ResourceNotFoundError('missing')
            entity, etag = self.rows[partition_key, row_key]
            value = TableEntity(deepcopy(entity))
            value._metadata = {'etag': str(etag)}
            return value

    def create_entity(self, entity):
        with self.lock:
            key = entity['PartitionKey'], entity['RowKey']
            if key in self.rows:
                raise ResourceExistsError('exists')
            self.rows[key] = (deepcopy(entity), 1)

    def update_entity(self, entity, *, etag, mode, match_condition):
        with self.lock:
            key = entity['PartitionKey'], entity['RowKey']
            _, actual = self.rows[key]
            if etag != str(actual):
                self.conflicts += 1
                raise ResourceModifiedError('etag mismatch')
            self.rows[key] = (deepcopy(entity), actual + 1)


@pytest.fixture
def setup(monkeypatch):
    r = load('rentals')
    table = Table()
    shared = types.ModuleType('shared')
    shared.rentals = r
    shared.db = types.SimpleNamespace(_sessions=table, get_legacy_responses=lambda sid: [])
    monkeypatch.setitem(sys.modules, 'shared', shared)
    return load('booking_store'), r, table


def test_concurrent_last_pair_is_atomic(setup):
    store, r, table = setup
    initial = r.initial_state()
    initial['people'] = {str(uid): {'version': 0, 'attendance': None, 'current_student': True} for uid in (1, 2)}
    table.create_entity({'PartitionKey': 'BOOKING', 'RowKey': 'test', 'payload': json.dumps(initial)})
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


def test_duplicate_mutation_is_idempotent(setup):
    store, r, _ = setup
    operation = lambda state: r.apply_action(state, {'id': 1}, 'sit_student', 1)
    assert store.mutate('test', operation) == store.mutate('test', operation)


def test_oversize_state_does_not_overwrite(setup):
    store, r, _ = setup
    store.mutate('test', lambda state: state)
    with pytest.raises(r.BookingError, match='storage is full'):
        store.mutate('test', lambda state: {**state, 'too_large': 'x' * 60000})
    assert 'too_large' not in store.read('test')


def test_deleted_session_freezes_bookings_and_retains_data(setup):
    store, r, _ = setup
    store.mutate('test', lambda state: r.apply_action(state, {'id': 1}, 'declare_student', 1))
    store.mutate('test', lambda state: r.apply_action(state, {'id': 1}, 'reserve', 2, requested=38, allocated=38))
    store.close_deleted_session('test')
    assert store.read('test')['people']['1']['rental']['allocated'] == 38
    with pytest.raises(r.BookingError, match='deleted'):
        store.mutate('test', lambda state: r.apply_action(state, {'id': 1}, 'cancel_rental', 3))
    store.close_deleted_session('test')  # Safe confirmation retry.
    assert store.read('test')['deleted'] is True


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
