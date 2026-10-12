"""Exported student status follows the participant's declaration, not the registry."""
import types

import pytest

from tests.test_deleted_session_export import load_exporter


@pytest.mark.parametrize('category,registry_status,expected', [
    ('sit_student', False, True), ('non_sit', True, False), ('rental_skates', False, True),
])
@pytest.mark.parametrize('has_handle', [False, True])
def test_csv_uses_declaration_not_registry(monkeypatch, category, registry_status, expected, has_handle):
    responses = [{'telegram_id': 42, 'username': 'member' if has_handle else None,
                  'first_name': 'Declared user', 'category': category}]
    def member(handle):
        assert has_handle
        return {'is_sit_student': registry_status, 'full_name': 'Registry Name'}
    db = types.SimpleNamespace(get_member=member, get_responses=lambda sid: responses)
    exporter = load_exporter(monkeypatch, db)
    people = exporter._build_attendees(responses)
    assert people[0]['is_sit_student'] is expected
    assert people[0]['full_name'] == ('Registry Name' if has_handle else 'Declared user')
