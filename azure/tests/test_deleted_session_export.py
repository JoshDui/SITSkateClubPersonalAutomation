import importlib.util
import sys
import types
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def mock_cloud_runtime(monkeypatch):
    functions = types.ModuleType('azure.functions')
    storage = types.ModuleType('azure.storage')
    storage.__path__ = []
    blob = types.ModuleType('azure.storage.blob')
    blob.BlobServiceClient = type('BlobServiceClient', (), {})
    monkeypatch.setitem(sys.modules, 'azure.functions', functions)
    monkeypatch.setitem(sys.modules, 'azure.storage', storage)
    monkeypatch.setitem(sys.modules, 'azure.storage.blob', blob)


def test_deleted_session_is_not_exported(monkeypatch):
    shared = types.ModuleType('shared')
    calls = []
    shared.config = types.SimpleNamespace()
    shared.db = types.SimpleNamespace(get_session=lambda sid: None,
                                     get_responses=lambda sid: calls.append(sid))
    monkeypatch.setitem(sys.modules, 'shared', shared)
    path = Path(__file__).resolve().parents[1] / 'functionapp/exporter/__init__.py'
    spec = importlib.util.spec_from_file_location('deleted_export_test', path)
    exporter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(exporter)
    exporter.main(types.SimpleNamespace(get_json=lambda: {'session_id': 'deleted'}))
    assert not calls


def test_deletion_during_csv_build_prevents_upload(monkeypatch):
    shared = types.ModuleType('shared')
    sessions = iter([{'id': 'test', 'session_date': '2099-01-01'}, None])
    shared.config = types.SimpleNamespace()
    shared.db = types.SimpleNamespace(get_session=lambda sid: next(sessions), get_responses=lambda sid: [])
    monkeypatch.setitem(sys.modules, 'shared', shared)
    path = Path(__file__).resolve().parents[1] / 'functionapp/exporter/__init__.py'
    spec = importlib.util.spec_from_file_location('deleted_export_recheck_test', path)
    exporter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(exporter)
    calls = []
    exporter._build_attendees = lambda rows: []
    exporter._build_csv = lambda *args: b'csv'
    exporter._upload_csv_to_blob = lambda *args: calls.append(args)
    exporter.main(types.SimpleNamespace(get_json=lambda: {'session_id': 'test'}))
    assert not calls
