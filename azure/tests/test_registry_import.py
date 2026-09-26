import importlib.util
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1] / 'functionapp/shared'
ACK = 'I have Read and Acknowledged the updated rules in SIT Inline Skating Club and will abide by them.'
HEADERS = ['Full Name:', 'Telegram Handle:', 'Column', 'Id', 'SIT Student/alumni?', 'SIT Student ID']


@pytest.fixture
def setup(monkeypatch):
    records, writes = {}, []
    def upsert(entity, **kwargs):
        writes.append(entity)
        records[entity['RowKey']] = entity
    shared = types.ModuleType('shared')
    shared.db = types.SimpleNamespace(get_member=lambda name: records.get(name),
                                      _members=types.SimpleNamespace(upsert_entity=upsert))
    monkeypatch.setitem(sys.modules, 'shared', shared)
    spec = importlib.util.spec_from_file_location('registry_test', ROOT / 'registry_import.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    def workbook(rows):
        sheet = types.SimpleNamespace(values=iter(rows))
        book = types.SimpleNamespace(sheetnames=[], worksheets=[sheet], close=lambda: None)
        monkeypatch.setattr(module.openpyxl, 'load_workbook', lambda *args, **kwargs: book)
    return module, workbook, writes


def test_acknowledgement_not_just_presence(setup):
    module, workbook, writes = setup
    workbook([HEADERS, ['Test', '@TESTER', ACK, 1, 'Yes', '123'], ['Second', 'otheruser', 'No', 2, 'No', '']])
    result = module.import_from_file('fake.xlsx')
    assert result['acknowledged'] == 1
    assert len(writes) == 2
    assert writes[0]['username'] == 'tester'
    assert writes[0]['rules_acknowledged'] is True
    assert 'current_student' not in writes[0]
    assert writes[1]['rules_acknowledged'] is False


def test_conflicting_handle_is_not_verified(setup):
    module, workbook, writes = setup
    workbook([HEADERS, ['Test', 'tester', ACK, 1, 'Yes', '123'], ['Other', '@tester', ACK, 2, 'Yes', '456']])
    result = module.import_from_file('fake.xlsx')
    assert result['ambiguous'] == 2
    assert result['acknowledged'] == 0
    assert all(record['ambiguous'] for record in writes)


def test_invalid_handles_skipped(setup):
    module, workbook, writes = setup
    workbook([HEADERS, ['Test', 'N/A', ACK, 1], ['Other', '12345678', ACK, 2]])
    assert module.import_from_file('fake.xlsx')['skipped'] == 2
    assert not writes


@pytest.mark.parametrize('rows', [[], [['Full Name:', 'Telegram Handle:'], ['Test', 'tester']]])
def test_wrong_or_empty_workbook_rejected_before_writes(setup, rows):
    module, workbook, writes = setup
    workbook(rows)
    with pytest.raises(ValueError):
        module.import_from_file('fake.xlsx')
    assert not writes


def test_changed_identity_remains_blocked_across_reimports(setup):
    module, workbook, writes = setup
    workbook([HEADERS, ['Original', 'tester', ACK, 1, 'Yes', '123']])
    assert module.import_from_file('fake.xlsx')['acknowledged'] == 1
    replacement = [HEADERS, ['Different Person', 'tester', ACK, 2, 'Yes', '456']]
    for _ in range(2):
        workbook(replacement)
        result = module.import_from_file('fake.xlsx')
        assert result['acknowledged'] == 0
        assert result['ambiguous'] == 1
        assert writes[-1]['identity_conflict'] is True
