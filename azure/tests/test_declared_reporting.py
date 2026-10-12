import importlib.util
import sys
import types
from pathlib import Path

import pytest

from test_deleted_session_export import mock_cloud_runtime

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('category,registry_status,expected', [
    ('sit_student', False, True), ('non_sit', True, False), ('rental_skates', False, True),
])
@pytest.mark.parametrize('has_handle', [False, True])
def test_csv_and_excel_use_declaration_not_registry(monkeypatch, category, registry_status, expected, has_handle):
    responses = [{'telegram_id': 42, 'username': 'member' if has_handle else None,
                  'first_name': 'Declared user', 'category': category}]
    def member(handle):
        assert has_handle
        return {'is_sit_student': registry_status, 'full_name': 'Registry Name'}
    db = types.SimpleNamespace(get_member=member, get_responses=lambda sid: responses)
    shared = types.ModuleType('shared')
    shared.config = types.SimpleNamespace()
    shared.db = db
    monkeypatch.setitem(sys.modules, 'shared', shared)
    exporter = load('declaration_exporter', ROOT / 'functionapp/exporter/__init__.py')
    report = load('declaration_report', ROOT / 'scripts/build_attendance_report.py')
    csv_people = exporter._build_attendees(responses)
    excel_people = report._build_attendees_for_session({'id': 'session'}, db)
    assert csv_people == excel_people
    assert csv_people[0]['is_sit_student'] is expected
    assert csv_people[0]['full_name'] == ('Registry Name' if has_handle else 'Declared user')
