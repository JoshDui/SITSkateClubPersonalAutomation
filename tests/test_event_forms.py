"""Validation rules for special-event answers (pure, no AWS)."""
import importlib.util
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

SPEC = importlib.util.spec_from_file_location(
    'event_forms_test', Path(__file__).resolve().parents[1] / 'shared/event_forms.py')
ef = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ef)


@pytest.mark.parametrize('raw,expected', [
    ('T567D', 'T567D'), (' s123a ', 'S123A'), ('F001X', 'F001X'), ('g999k', 'G999K'),
    ('M123Q', 'M123Q'), ('T 567 D', 'T567D'),
])
def test_partial_nric_fin_accepted(raw, expected):
    assert ef.masked_id(raw) == expected


@pytest.mark.parametrize('raw', ['S1234567D', 's1234567d', 'T 1234 567 D', 'F1234567N', 'T1234567'])
def test_full_or_longer_ids_are_refused_with_a_warning(raw):
    with pytest.raises(ef.FormError, match='do NOT send your full'):
        ef.masked_id(raw)


@pytest.mark.parametrize('raw', ['', 'A567D', 'T56D', 'T5678', '567D', 'TT67D'])
def test_malformed_partial_ids_are_refused(raw):
    with pytest.raises(ef.FormError):
        ef.masked_id(raw)


def test_errors_never_echo_the_input():
    for parser, raw in ((ef.masked_id, 'S1234567D'), (ef.masked_id, 'X999Z'),
                        (ef.student_number, '24022011'), (ef.full_name, '1234')):
        with pytest.raises(ef.FormError) as exc:
            parser(raw)
        assert raw not in str(exc.value)


@pytest.mark.parametrize('raw,ok', [('2402201', True), (' 2402 201 ', True), ('240220', False),
                                    ('24022011', False), ('abcdefg', False)])
def test_student_number(raw, ok):
    if ok:
        assert ef.student_number(raw) == '2402201'
    else:
        with pytest.raises(ef.FormError):
            ef.student_number(raw)


def test_names_and_text_limits():
    assert ef.full_name('  Tan   Ah  Kow ') == 'Tan Ah Kow'
    for bad in ('', 'A', '12345', 'x' * 81):
        with pytest.raises(ef.FormError):
            ef.full_name(bad)
    assert ef.details('  Line one\nLine two  ') == 'Line one\nLine two'
    with pytest.raises(ef.FormError):
        ef.details('x' * 1001)
    with pytest.raises(ef.FormError):
        ef.title('x' * 81)


def test_dates_and_times():
    today = date(2026, 10, 12)
    assert ef.event_date('2026-10-31', today) == '2026-10-31'
    assert ef.event_date('2026-10-12', today) == '2026-10-12'
    for bad in ('2026-10-11', '31/10/2026', '2026-02-30'):
        with pytest.raises(ef.FormError):
            ef.event_date(bad, today)
    assert ef.clock('9:05') == '09:05'
    with pytest.raises(ef.FormError):
        ef.clock('7pm')
    with pytest.raises(ef.FormError):
        ef.end_after('19:00', '19:00')
    now = datetime(2026, 10, 12, 18, 0, tzinfo=ZoneInfo('Asia/Singapore'))
    ef.starts_in_future('2026-10-12', '19:00', now)
    with pytest.raises(ef.FormError):
        ef.starts_in_future('2026-10-12', '17:59', now)


def test_year_of_study_choices_match_the_ose_form():
    assert [ef.year_of_study(k) for k in ('1', '2', '3', '4', 'other')] == [
        'Year 1', 'Year 2', 'Year 3', 'Year 4', 'Other']
    with pytest.raises(ef.FormError):
        ef.year_of_study('5')


@pytest.mark.parametrize('raw', ['Tan S1234567D', 'S1234567D', 'Tan 2402201', 'Ah Kow 3'])
def test_names_with_digits_are_refused(raw):
    with pytest.raises(ef.FormError, match='cannot contain numbers'):
        ef.full_name(raw)


def test_programme_list():
    spec = importlib.util.spec_from_file_location(
        'programmes_test', Path(__file__).resolve().parents[1] / 'shared/programmes.py')
    programmes = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(programmes)
    assert list(programmes.PROGRAMMES) == list(programmes.CLUSTER_ORDER)
    assert sum(len(v) for v in programmes.PROGRAMMES.values()) == 60
    for items in programmes.PROGRAMMES.values():
        labels = [label for _, label in items]
        assert labels == sorted(labels, key=str.casefold)
        assert len(set(labels)) == len(labels) and max(map(len, labels)) <= 41
        assert all(name == name.strip() and '\ufffd' not in name for name, _ in items)
        # Longest callback: e:<6-hex nonce>:prog:<index>
        assert len(f'e:abcdef:prog:{len(items) - 1}'.encode()) <= 64
    with pytest.raises(ValueError):
        programmes.programme('ENG', 99)
