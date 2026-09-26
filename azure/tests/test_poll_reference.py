"""Exercise the real Azure SDK encoding, not only a fake table accepting ints."""
import importlib.util
import sys
import types
from pathlib import Path

import azure.data.tables
import azure.identity
from azure.data.tables._serialize import _add_entity_properties
from azure.data.tables._deserialize import _convert_to_entity


def test_supergroup_poll_reference_round_trips_through_azure_sdk(monkeypatch):
    wire = {}
    def upsert(entity, **kwargs):
        wire.update(_add_entity_properties(entity))
    table = types.SimpleNamespace(upsert_entity=upsert,
        get_entity=lambda **kw: _convert_to_entity(wire))
    monkeypatch.setattr(azure.data.tables, 'TableServiceClient', lambda **kw: types.SimpleNamespace(get_table_client=lambda name: table))
    monkeypatch.setattr(azure.identity, 'DefaultAzureCredential', lambda: None)
    shared = types.ModuleType('shared')
    shared.config = types.SimpleNamespace(COSMOS_ENDPOINT='https://example.invalid', TABLE_MEMBERS='members',
        TABLE_SESSIONS='sessions', TABLE_RESPONSES='responses', attendance_poll_chat_id=lambda: -1001593198353)
    monkeypatch.setitem(sys.modules, 'shared', shared)
    path = Path(__file__).resolve().parents[1] / 'functionapp/shared/db.py'
    spec = importlib.util.spec_from_file_location('poll_reference_db_test', path)
    db = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(db)
    db.set_poll_message_id('test-session', 4901)
    session = db.get_session('test-session')
    assert session['poll_chat_id'] == -1001593198353
    assert type(session['poll_chat_id']) is int
    assert session['poll_message_id'] == 4901
    assert wire['poll_chat_id@odata.type'] == 'Edm.Int64'
