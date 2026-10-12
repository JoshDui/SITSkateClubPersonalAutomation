"""Exporter idempotency and delivery to admins' private chats."""
import importlib.util
import sys
import types
from pathlib import Path

import pytest

EXPORTER = Path(__file__).resolve().parents[1] / 'services/exporter/handler.py'
SESSION = {'id': 'test', 'session_date': '2099-01-01', 'start_time': '18:30', 'end_time': '21:30',
           'location': 'SIT @ Punggol Coast', 'group_name': 'SIT Inline Skate'}


def load_exporter(monkeypatch, db, admins=(101, 102), send=None, store=None):
    shared = types.ModuleType('shared')
    shared.config = types.SimpleNamespace(admin_ids=lambda: set(admins))
    if hasattr(db, 'get_session'):
        lookup = db.get_session
        db.get_session = lambda sid, **kwargs: lookup(sid)  # Fakes ignore include_deleted.
    shared.db = db
    shared.booking_store = store or types.SimpleNamespace(purge_event_pii=lambda sid: None)
    shared.telegram = types.SimpleNamespace(send_document=send or (lambda *a, **kw: {}))
    monkeypatch.setitem(sys.modules, 'shared', shared)
    spec = importlib.util.spec_from_file_location('exporter_test', EXPORTER)
    exporter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(exporter)
    return exporter


def test_deleted_session_is_not_exported(monkeypatch):
    calls = []
    db = types.SimpleNamespace(get_session=lambda sid: None, get_responses=lambda sid: calls.append(sid))
    exporter = load_exporter(monkeypatch, db)
    assert exporter.lambda_handler({'session_id': 'deleted'}, None)['status'] == 'skipped'
    assert not calls


def test_deletion_during_csv_build_prevents_delivery(monkeypatch):
    sessions = iter([SESSION, None])
    sent = []
    db = types.SimpleNamespace(get_session=lambda sid: next(sessions), get_responses=lambda sid: [])
    exporter = load_exporter(monkeypatch, db, send=lambda *a, **kw: sent.append(a))
    exporter.lambda_handler({'session_id': 'test'}, None)
    assert not sent


@pytest.mark.parametrize('flag', ['skipped', 'exported'])
def test_skipped_or_already_exported_session_is_a_noop(monkeypatch, flag):
    sent = []
    db = types.SimpleNamespace(get_session=lambda sid: {**SESSION, flag: 1}, get_responses=lambda sid: [])
    exporter = load_exporter(monkeypatch, db, send=lambda *a, **kw: sent.append(a))
    exporter.lambda_handler({'session_id': 'test'}, None)
    assert not sent


def test_csv_sent_to_every_admin_then_marked_exported(monkeypatch):
    sent, marked = [], []
    responses = [{'telegram_id': 42, 'username': None, 'first_name': 'Declared', 'category': 'sit_student'}]
    db = types.SimpleNamespace(get_session=lambda sid: dict(SESSION), get_responses=lambda sid: responses,
                               get_member=lambda handle: None, mark_session_exported=marked.append)
    exporter = load_exporter(monkeypatch, db, send=lambda *a, **kw: sent.append((a, kw)))
    result = exporter.lambda_handler({'session_id': 'test'}, None)
    assert result == {'status': 'exported', 'session_id': 'test', 'delivered': 2}
    assert [a[0] for a, _ in sent] == [101, 102]
    filename, content = sent[0][0][1], sent[0][0][2]
    assert filename == '2099-01-01_test.csv'
    assert content.startswith('﻿session_date,'.encode())
    assert b'Declared' in content
    assert '1 attendees' in sent[0][1]['caption']
    assert marked == ['test']


def test_partial_delivery_still_marks_exported(monkeypatch):
    marked = []
    def send(chat_id, *args, **kwargs):
        if chat_id == 101:
            raise RuntimeError("Forbidden: bot can't initiate conversation with a user")
    db = types.SimpleNamespace(get_session=lambda sid: dict(SESSION), get_responses=lambda sid: [],
                               get_member=lambda handle: None, mark_session_exported=marked.append)
    exporter = load_exporter(monkeypatch, db, send=send)
    assert exporter.lambda_handler({'session_id': 'test'}, None)['delivered'] == 1
    assert marked == ['test']


def test_no_delivery_raises_for_retry_and_is_not_marked(monkeypatch):
    marked = []
    def send(*args, **kwargs):
        raise RuntimeError('network down')
    db = types.SimpleNamespace(get_session=lambda sid: dict(SESSION), get_responses=lambda sid: [],
                               get_member=lambda handle: None, mark_session_exported=marked.append)
    exporter = load_exporter(monkeypatch, db, send=send)
    with pytest.raises(RuntimeError, match='reached no admins'):
        exporter.lambda_handler({'session_id': 'test'}, None)
    assert not marked


def test_missing_session_id_is_rejected(monkeypatch):
    exporter = load_exporter(monkeypatch, types.SimpleNamespace())
    assert exporter.lambda_handler({}, None)['status'] == 'error'
