import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location('rental_rules_test', Path(__file__).resolve().parents[1] / 'functionapp/shared/rentals.py')
r = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(r)
USER = {'id': 1, 'first_name': 'Test'}


def reserve(state=None, uid=1, update=1, requested=38, allocated=38, version=0):
    state = state or r.initial_state()
    state['people'].setdefault(str(uid), {'version': 0, 'attendance': None, 'current_student': True})
    return r.apply_action(state, {**USER, 'id': uid}, 'reserve', update,
                          requested=requested, allocated=allocated, version=version)


def test_inventory_and_confirmation():
    assert sum(r.INVENTORY.values()) == 60
    state = reserve()
    assert r.available(state)[38] == 0
    assert r.available(state, 1)[38] == 1
    assert [x['category'] for x in r.responses(state, 'session')] == ['sit_student', 'rental_skates']


def test_last_pair_cannot_be_double_booked():
    with pytest.raises(r.BookingError, match='no longer available'):
        reserve(reserve(), uid=2)


@pytest.mark.parametrize('requested,allocated', [(37, 36), (37, 39), (45, 46), (34, 35)])
def test_disallowed_sizes(requested, allocated):
    with pytest.raises(r.BookingError):
        reserve(requested=requested, allocated=allocated)


def test_one_size_up():
    state = reserve(requested=36, allocated=37)
    assert state['people']['1']['rental'] == {'requested': 36, 'allocated': 37, 'guards': False}


def test_failed_change_keeps_original_pair():
    state = reserve(requested=37, allocated=37)
    state = reserve(state, uid=2)
    with pytest.raises(r.BookingError):
        reserve(state, update=2, version=1)
    assert state['people']['1']['rental']['allocated'] == 37


def test_duplicate_and_stale_buttons():
    state = reserve()
    assert reserve(state) == state
    cancelled = r.apply_action(state, USER, 'cancel_rental', 2, version=1)
    assert cancelled['people']['1']['attendance'] == 'sit_student'
    with pytest.raises(r.BookingError, match='out of date'):
        reserve(cancelled, update=3)
    assert reserve(cancelled) == cancelled


def test_cancel_attendance_releases_pair():
    state = r.apply_action(reserve(), USER, 'cancel_attendance', 2, version=1)
    assert r.responses(state, 'session') == []
    assert r.available(state)[38] == 1


def test_duplicate_attendance_does_not_toggle_off():
    state = r.apply_action(r.initial_state(), USER, 'sit_student', 1)
    assert r.apply_action(state, USER, 'sit_student', 1) == state
    assert r.apply_action(state, USER, 'sit_student', 2)['people']['1']['attendance'] is None


def test_external_declaration_preserves_attendance_and_releases_rental():
    state = r.apply_action(reserve(), USER, 'declare_external', 2)
    assert state['people']['1']['attendance'] == 'non_sit'
    assert 'rental' not in state['people']['1']
    assert r.available(state)[38] == 1


def test_external_declaration_does_not_add_attendance():
    state = r.apply_action(r.initial_state(), USER, 'declare_external', 1)
    assert state['people']['1']['attendance'] is None


def test_reservation_requires_declaration_inside_atomic_operation():
    with pytest.raises(r.BookingError, match='current SIT student'):
        r.apply_action(r.initial_state(), USER, 'reserve', 1, requested=38, allocated=38)


def test_old_declaration_cannot_override_new_declaration():
    state = r.apply_action(r.initial_state(), USER, 'declare_external', 1)
    state = r.apply_action(state, USER, 'declare_student', 2)
    assert r.apply_action(state, USER, 'declare_external', 1)['people']['1']['current_student'] is True
