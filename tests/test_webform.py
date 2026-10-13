"""Web registration Lambda end to end: real handler and shared modules on moto;
only Telegram is faked. Requests are Lambda Function URL events."""
from __future__ import annotations

import csv
import io
import json
import re
from urllib.parse import urlencode, urlsplit

import pytest

from tests.test_events import ADMIN, EVENT_DAY, MEMBER, ROOT, _load, _state_items, env  # noqa: F401


@pytest.fixture
def web(env):  # noqa: F811
    handler = _load('webform_handler_test', ROOT / 'services/webform/handler.py')
    session = env.create_event()
    from shared import web_tokens
    sid, token = session['id'], web_tokens.event_token(session['id'])
    from shared import programmes
    ict = [label for _, label in programmes.PROGRAMMES['ICT']].index('ICT (Software Engineering)')

    def request(method, path, query='', form=None, ip='203.0.113.7'):
        return handler.lambda_handler({
            'rawPath': path, 'rawQueryString': query, 'isBase64Encoded': False,
            'body': urlencode(form) if form is not None else '',
            'requestContext': {'http': {'method': method, 'sourceIp': ip},
                               'domainName': 'form.example.test'},
        }, None)

    def fresh_sub(category):
        page = request('GET', f'/e/{sid}/{category}', f'k={token}')['body']
        return re.search(r'name="sub" value="([^"]+)"', page).group(1)

    def sit_form(**overrides):
        form = {'sub': fresh_sub('sit'), 'website': '', 'name': 'Tan Ah Kow', 'student_no': '2402201',
                'handle': '@Skater_SG', 'programme': f'ICT:{ict}', 'year': '2', 'skates': 'no'}
        form.update(overrides)
        return form

    def alumni_form(**overrides):
        form = {'sub': fresh_sub('alumni'), 'website': '', 'name': 'Lim Mei Ling',
                'masked_id': 'T567D', 'handle': 'meiling_alum'}
        form.update(overrides)
        return form

    def post(category, form, ip='203.0.113.7'):
        return request('POST', f'/e/{sid}/{category}', f'k={token}', form, ip)

    def person(handle):
        people = env.store.read(sid)['people']
        return next(p for p in people.values() if p.get('username') == handle)

    return env | {'handler': handler, 'sid': sid, 'token': token, 'request': request,
                  'sit_form': sit_form, 'alumni_form': alumni_form, 'post': post, 'person': person}


class _NS(dict):
    __getattr__ = dict.__getitem__


@pytest.fixture
def w(web):
    return _NS(web)


def test_pages_require_the_event_token(w):
    ok = w.request('GET', f'/e/{w.sid}', f'k={w.token}')
    assert ok['statusCode'] == 200 and 'Halloween Skate' in ok['body']
    assert f'/e/{w.sid}/sit?k={w.token}' in ok['body']
    for query in ('', 'k=wrong', f'k={w.token}x'):
        assert w.request('GET', f'/e/{w.sid}', query)['statusCode'] == 404
    assert w.request('GET', '/e/notasession', f'k={w.token}')['statusCode'] == 404
    assert w.request('GET', '/', '')['statusCode'] == 404


def test_security_headers_and_escaping(w):
    from shared import web_tokens
    sid = w.db.create_session(EVENT_DAY, '19:00', '22:00', '<b>Court</b>', group_key='event',
                              kind='event', title='<script>alert(1)</script>')
    resp = w.request('GET', f'/e/{sid}', 'k=' + web_tokens.event_token(sid))
    assert '<script>' not in resp['body'] and '&lt;script&gt;' in resp['body']
    headers = resp['headers']
    assert "default-src 'none'" in headers['Content-Security-Policy']
    assert headers['Referrer-Policy'] == 'no-referrer' and headers['X-Frame-Options'] == 'DENY'
    assert headers['Cache-Control'] == 'no-store'


def test_sit_web_registration_appears_like_telegram(w):
    resp = w.post('sit', w.sit_form())
    assert resp['statusCode'] == 200 and 'You are registered' in resp['body']
    person = w.person('skater_sg')
    assert person['first_name'] == '@skater_sg' and person['attendance'] == 'sit_student'
    assert person['event_form']['source'] == 'web'
    assert person['event_form']['programme'].endswith('majoring in Software Engineering')
    assert person['event_form']['year'] == 'Year 2'
    pid = next(k for k, p in w.store.read(w.sid)['people'].items() if p.get('username') == 'skater_sg')
    assert int(pid) < 0
    announcement = w.tg['edits'][-1][0][2]
    assert '@skater_sg' in announcement and '2402201' not in announcement
    cancel = re.search(r'https://form\.example\.test/c/[^"<]+', resp['body']).group(0)
    assert f'/c/{w.sid}/{pid}/' in cancel


def test_alumni_full_nric_is_refused_not_echoed_not_stored(w):
    resp = w.post('alumni', w.alumni_form(masked_id='S1234567D'))
    assert resp['statusCode'] == 400 and 'do NOT send your full NRIC' in resp['body']
    assert '1234567' not in resp['body'] and 'Lim Mei Ling' in resp['body']  # Name kept, ID not.
    assert '1234567' not in json.dumps(_state_items(), default=str)
    assert w.post('alumni', w.alumni_form())['statusCode'] == 200
    assert w.person('meiling_alum')['event_form']['masked_id'] == 'T567D'


def test_student_number_is_never_echoed_after_an_error(w):
    resp = w.post('sit', w.sit_form(year=''))
    assert resp['statusCode'] == 400 and '2402201' not in resp['body']


def test_duplicates_across_telegram_and_web_are_refused(w):
    w.register_sit(sid=w.sid)  # Telegram: user500, student number 2402201
    resp = w.post('sit', w.sit_form(handle='someone_else'))
    assert 'student number is already registered' in resp['body']
    resp = w.post('sit', w.sit_form(student_no='2400001', handle=f'user{MEMBER}'))
    assert 'username is already registered' in resp['body']


def test_resubmitting_the_same_form_is_idempotent(w):
    form = w.sit_form()
    first, second = w.post('sit', form), w.post('sit', form)
    assert first['statusCode'] == second['statusCode'] == 200
    link = re.compile(r'https://form\.example\.test/c/[^"<]+')
    assert link.search(first['body']).group(0) == link.search(second['body']).group(0)
    assert sum(p.get('username') == 'skater_sg' for p in w.store.read(w.sid)['people'].values()) == 1


def test_web_rental_needs_consent_for_one_size_up(w):
    resp = w.post('sit', w.sit_form(skates='yes', size='36'))  # EU 36 stock is 0; EU 37 has 8.
    assert resp['statusCode'] == 400 and 'EU 37 is available' in resp['body']
    resp = w.post('sit', w.sit_form(skates='yes', size='36', upsize='1', guards='1'))
    assert resp['statusCode'] == 200 and 'EU 37 (requested EU 36)' in resp['body']
    assert w.person('skater_sg')['rental'] == {'requested': 36, 'allocated': 37, 'guards': True}
    assert 'Rental skates: (1' in w.tg['edits'][-1][0][2]


def test_web_and_telegram_share_the_last_pair(w):
    w.post('sit', w.sit_form(skates='yes', size='38'))  # EU 38 has exactly 1 pair.
    resp = w.post('sit', w.sit_form(skates='yes', size='38', student_no='2400002', handle='second_skater'))
    assert 'EU 39 is available' in resp['body']


def test_cancel_link_releases_the_pair(w):
    resp = w.post('sit', w.sit_form(skates='yes', size='40'))
    path = urlsplit(re.search(r'https://form\.example\.test/c/[^"<]+', resp['body']).group(0)).path
    assert 'Yes, cancel my registration' in w.request('GET', path)['body']
    tampered = path[:-1] + ('A' if path[-1] != 'A' else 'B')
    assert w.request('POST', tampered, form={})['statusCode'] == 404
    assert 'Registration cancelled' in w.request('POST', path, form={})['body']
    person = w.person('skater_sg')
    assert person['attendance'] is None and 'rental' not in person and 'event_form' not in person
    assert 'already cancelled' in w.request('GET', path)['body']


def test_honeypot_saves_nothing(w):
    resp = w.post('sit', w.sit_form(website='http://spam.example'))
    assert resp['statusCode'] == 200 and 'Thank you' in resp['body']
    assert w.store.read(w.sid) is None  # Nothing was written at all.


def test_requests_are_rate_limited_per_ip_in_memory(w):
    codes = [w.request('GET', '/nowhere', ip='198.51.100.9')['statusCode'] for _ in range(121)]
    assert codes[:120] == [404] * 120 and codes[120] == 429
    assert w.post('alumni', w.alumni_form(), ip='198.51.100.10')['statusCode'] == 200
    # No rate-limit state is written to the database.
    assert not [i for i in _state_items() if i['pk'] == 'RATE']


def test_registration_closes_when_the_event_starts(w):
    w.db.update_session_details(w.sid, '2020-01-01', '19:00', '22:00', 'Court')
    resp = w.request('GET', f'/e/{w.sid}/sit', f'k={w.token}')
    assert 'Registration closed' in resp['body']


def test_export_includes_web_rows_with_source(w):
    w.register_sit(sid=w.sid)
    w.post('alumni', w.alumni_form())
    exporter = _load('webform_exporter_test', ROOT / 'services/exporter/handler.py')
    exporter.lambda_handler({'session_id': w.sid}, None)
    rows = list(csv.reader(io.StringIO(w.tg['documents'][0][2].decode('utf-8-sig'))))
    header = rows[0]
    by_source = {row[header.index('Source')]: row for row in rows[1:]}
    assert by_source['Telegram'][header.index('Telegram Handle')] == f'user{MEMBER}'
    web_row = by_source['Web']
    assert web_row[header.index('Telegram Handle')] == 'meiling_alum'
    assert web_row[header.index('Partial NRIC/FIN')] == 'T567D'
    assert 'T567D' not in json.dumps(_state_items(), default=str)  # Purged after export.


def test_eventqr_sends_a_png_of_the_tokenised_link(w, monkeypatch):
    from shared import config, qr, telegram
    monkeypatch.setattr(config, 'WEBFORM_BASE_URL', 'https://form.example.test/')
    photos = []
    monkeypatch.setattr(telegram, 'send_photo', lambda chat, name, content, caption=None:
                        photos.append((chat, content, caption)))
    w.dm(ADMIN, '/eventqr')
    chat, content, caption = photos[0]
    link = f'https://form.example.test/e/{w.sid}?k={w.token}'
    assert chat == ADMIN and link in caption
    assert content.startswith(b'\x89PNG') and content == qr.png(link)
    w.dm(MEMBER, '/eventqr')
    assert len(photos) == 1  # Admin only.


# ── Security review fixes ────────────────────────────────────────────────────

def test_forged_links_are_refused_without_touching_the_database(w, monkeypatch):
    def no_db(*args, **kwargs):
        raise AssertionError('database read before the token check')
    monkeypatch.setattr(w.db, 'get_session', no_db)
    assert w.request('GET', f'/e/{w.sid}', 'k=forged')['statusCode'] == 404
    assert w.request('GET', f'/e/{w.sid}/sit', 'k=forged')['statusCode'] == 404
    assert w.request('POST', f'/e/{w.sid}/sit', 'k=forged', {'name': 'x'})['statusCode'] == 404
    assert w.request('GET', f'/c/{w.sid}/-123/' + 'A' * 32)['statusCode'] == 404


def test_unreadable_bodies_are_bad_requests(w):
    resp = w.handler.lambda_handler({
        'rawPath': f'/e/{w.sid}/sit', 'rawQueryString': f'k={w.token}', 'isBase64Encoded': True,
        'body': '***not base64***', 'requestContext': {'http': {'method': 'POST', 'sourceIp': '1.1.1.1'},
                                                        'domainName': 'form.example.test'}}, None)
    assert resp['statusCode'] == 400
    too_many = '&'.join(f'f{i}=1' for i in range(40))
    resp = w.handler.lambda_handler({
        'rawPath': f'/e/{w.sid}/sit', 'rawQueryString': f'k={w.token}', 'isBase64Encoded': False,
        'body': too_many, 'requestContext': {'http': {'method': 'POST', 'sourceIp': '1.1.1.1'},
                                               'domainName': 'form.example.test'}}, None)
    assert resp['statusCode'] == 400


def test_unexpected_errors_reach_the_lambda_error_metric(w, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError('DynamoDB unavailable')
    monkeypatch.setattr(w.db, 'get_session', boom)
    with pytest.raises(RuntimeError):
        w.request('GET', f'/e/{w.sid}', f'k={w.token}')


@pytest.mark.parametrize('field,value', [('student_no', '\u0662\u0664\u0660\u0662\u0662\u0660\u0661'),
                                         ('student_no', '\uff12\uff14\uff10\uff12\uff12\uff10\uff11')])
def test_non_ascii_digits_are_refused(w, field, value):
    resp = w.post('sit', w.sit_form(**{field: value}))
    assert resp['statusCode'] == 400 and '7 digits' in resp['body']


def test_duplicate_check_runs_from_telegram_too(w):
    assert w.post('sit', w.sit_form())['statusCode'] == 200  # Web: student number 2402201
    w.register_sit(sid=w.sid)  # Telegram, same student number
    assert 'already registered' in w.tg['sent'][-1]['text']
    assert not w.store.read(w.sid)['people'].get(str(MEMBER), {}).get('attendance')  # Refused before any write.


def test_replaying_a_form_after_cancelling_does_not_re_register(w):
    form = w.sit_form()
    resp = w.post('sit', form)
    path = urlsplit(re.search(r'https://form\.example\.test/c/[^"<]+', resp['body']).group(0)).path
    w.request('POST', path, form={})
    replay = w.post('sit', form)
    assert replay['statusCode'] == 400 and 'already used and then cancelled' in replay['body']
    assert w.person('skater_sg')['attendance'] is None


def test_web_handle_matching_a_telegram_registrant_is_flagged(w):
    w.post('alumni', w.alumni_form(handle=f'user{MEMBER}'))  # Typed someone else's handle first.
    w.register_sit(sid=w.sid)  # The real @user500 registers through Telegram.
    exporter = _load('webform_exporter_flag_test', ROOT / 'services/exporter/handler.py')
    exporter.lambda_handler({'session_id': w.sid}, None)
    rows = list(csv.reader(io.StringIO(w.tg['documents'][0][2].decode('utf-8-sig'))))
    header = rows[0]
    web_row = next(r for r in rows[1:] if r[header.index('Source')] == 'Web')
    assert 'please check' in web_row[header.index('Remarks')]


def test_purge_removes_web_handles_too(w):
    w.post('alumni', w.alumni_form())
    w.store.purge_event_pii(w.sid)
    dump = json.dumps(_state_items(), default=str)
    assert 'meiling_alum' not in dump and 'T567D' not in dump
