"""Pure validation for special-event setup and registration answers.

Each parser returns the normalised value or raises FormError with a message
safe to show the user. Messages never echo the input back: answers can be
personal data (student numbers, partial NRIC/FIN).

Alumni identification follows the campus-security requirement: a partial
NRIC/FIN of the prefix letter, the last three digits and the checksum letter
(e.g. T567D). Full numbers are rejected and never stored.
"""
from __future__ import annotations

import re
from datetime import date, datetime

TITLE_MAX = 80
LOCATION_MAX = 120
DETAILS_MAX = 1000
NAME_MAX = 80
# Button key → value. 'Year 1'..'Year 4' match the OSE attendance form's
# Year of Study dropdown; 'Other' covers extended or part-time programmes.
YEARS = {'1': 'Year 1', '2': 'Year 2', '3': 'Year 3', '4': 'Year 4', 'other': 'Other'}

_MASKED_ID = re.compile(r'[STFGM]\d{3}[A-Z]')
_FULL_ID = re.compile(r'[STFGM]\d{7}[A-Z]')
_STUDENT_NUMBER = re.compile(r'\d{7}')


class FormError(ValueError):
    pass


def _one_line(text, limit, what):
    value = ' '.join((text or '').split())
    if not value:
        raise FormError(f'Please send the {what}.')
    if len(value) > limit:
        raise FormError(f'The {what} must be at most {limit} characters.')
    return value


def title(text):
    return _one_line(text, TITLE_MAX, 'event title')


def location(text):
    return _one_line(text, LOCATION_MAX, 'location')


def details(text):
    value = (text or '').strip()
    if not value:
        raise FormError('Please send the event details.')
    if len(value) > DETAILS_MAX:
        raise FormError(f'The details must be at most {DETAILS_MAX} characters.')
    return value


def event_date(text, today):
    try:
        value = datetime.strptime((text or '').strip(), '%Y-%m-%d').date()
    except ValueError:
        raise FormError('Please send the date as YYYY-MM-DD, e.g. 2026-10-31.') from None
    if value < today:
        raise FormError('The event date cannot be in the past.')
    return value.isoformat()


def clock(text):
    try:
        return datetime.strptime((text or '').strip(), '%H:%M').strftime('%H:%M')
    except ValueError:
        raise FormError('Please send the time as 24-hour HH:MM, e.g. 19:30.') from None


def end_after(start, end):
    if end <= start:
        raise FormError('The end time must be after the start time.')
    return end


def starts_in_future(session_date, start, now):
    starts = datetime.combine(date.fromisoformat(session_date),
                              datetime.strptime(start, '%H:%M').time(), tzinfo=now.tzinfo)
    if starts <= now:
        raise FormError('That start time has already passed. Please send a later start time.')


def full_name(text):
    value = _one_line(text, NAME_MAX, 'full name')
    if any(ch.isdigit() for ch in value):
        # Also keeps student numbers and NRIC/FIN out of the name field.
        raise FormError('Names cannot contain numbers. Please send your full name only.')
    if len(value) < 2 or not any(ch.isalpha() for ch in value):
        raise FormError('Please send your full name.')
    return value


def student_number(text):
    value = re.sub(r'\s', '', text or '')
    if not _STUDENT_NUMBER.fullmatch(value):
        raise FormError('Your SIT student number should be exactly 7 digits.')
    return value


def masked_id(text):
    """Accept only prefix + last 3 digits + checksum letter (S/T/F/G/M)."""
    value = re.sub(r'\s', '', text or '').upper()
    if _FULL_ID.fullmatch(value) or len(value) > 5:
        raise FormError('Please do NOT send your full NRIC/FIN. Send only the first letter, '
                        'the last 3 digits and the last letter, e.g. T567D.')
    if not _MASKED_ID.fullmatch(value):
        raise FormError('Please send the first letter, the last 3 digits and the last letter '
                        'of your NRIC/FIN, e.g. T567D.')
    return value


def year_of_study(key):
    if key not in YEARS:
        raise FormError('Please choose your year of study from the buttons.')
    return YEARS[key]
