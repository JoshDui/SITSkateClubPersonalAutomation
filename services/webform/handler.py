"""
Web registration Lambda for special events (public Function URL).

The QR code an admin gets after posting an event (/eventqr) opens
/e/<session_id>?k=<token>. Registrants fill the same fields as the Telegram
form, validated by the same parsers (shared/event_forms.py), and are written
to the same booking document (rentals.register_web), so they appear on the
group announcement, use the live skate stock, and go through the same CSV
export and personal-data purge.

Routes (GET shows, POST submits):
  /e/<sid>?k=…             event page with SIT / alumni choices
  /e/<sid>/sit?k=…         SIT student form
  /e/<sid>/alumni?k=…      alumni form
  /c/<sid>/<pid>/<token>   private cancel link from the confirmation page

Cost and availability on a public URL: event and cancel tokens are HMACs
(shared/web_tokens.py), so a wrong or random link is refused before any
database access. Requests are limited per IP in memory (keyed hash, no
storage), and the event and its stock display are cached per warm container
for a few seconds, so page views barely touch the provisioned capacity the
Telegram bot shares. The final stock check is still atomic.

Never logged: request bodies, form values, IP addresses.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import logging
import re
import secrets
import time
from collections import deque
from urllib.parse import parse_qs

from shared import booking_flow, db, programmes, rentals, web_tokens
from shared import booking_store as store
from shared import event_forms as ef
from shared import webform_pages as pages

log = logging.getLogger()
log.setLevel(logging.INFO)

_SID = r'([0-9A-HJKMNP-TV-Z]{26})'
_EVENT = re.compile(rf'^/e/{_SID}/?$')
_FORM = re.compile(rf'^/e/{_SID}/(sit|alumni)/?$')
_CANCEL = re.compile(rf'^/c/{_SID}/(-[0-9]{{1,20}})/([A-Za-z0-9_-]{{{web_tokens.CANCEL_TOKEN_LENGTH}}})/?$')
_SUBMISSION = re.compile(r'[A-Za-z0-9_-]{16,40}')
MAX_BODY_BYTES = 8 * 1024
# Per IP, per warm container. Generous: a whole campus can share one NAT address.
IP_LIMIT, IP_WINDOW = 120, 60
SESSION_CACHE_SECONDS, STOCK_CACHE_SECONDS = 30, 15

_HEADERS = {
    'Content-Type': 'text/html; charset=utf-8',
    'Content-Security-Policy': ("default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; "
                                "base-uri 'none'; frame-ancestors 'none'"),
    'X-Frame-Options': 'DENY',
    'X-Content-Type-Options': 'nosniff',
    'Referrer-Policy': 'no-referrer',  # The event token and cancel links stay out of Referer.
    'Cache-Control': 'no-store',
}

_hits: dict[str, deque] = {}
_cache: dict[tuple, tuple[float, object]] = {}


class BadRequest(Exception):
    pass


def _resp(status, html):
    return {'statusCode': status, 'headers': dict(_HEADERS), 'body': html}


def _not_found():
    return _resp(404, pages.message('Not found', 'This link is not valid. Please scan the QR code again.'))


def lambda_handler(event, context):
    """Unexpected exceptions propagate on purpose: Lambda counts them as
    errors, which drives the CloudWatch error alarm."""
    http = (event.get('requestContext') or {}).get('http') or {}
    method, path = http.get('method', 'GET'), event.get('rawPath') or '/'
    if method not in ('GET', 'POST'):
        return _resp(405, pages.message('Not allowed', 'Unsupported request.'))
    if _throttled(http.get('sourceIp', '')):
        return _resp(429, pages.message('Please wait', 'Too many requests. Try again in a minute.'))
    try:
        form = _form(event) if method == 'POST' else {}
    except BadRequest:
        return _resp(400, pages.message('Bad request', 'That submission could not be read.'))
    query = {k: v[0] for k, v in parse_qs(event.get('rawQueryString') or '', max_num_fields=5).items()}
    host = (event.get('requestContext') or {}).get('domainName', '')
    if match := _EVENT.match(path):
        return _event_page(match.group(1), query)
    if match := _FORM.match(path):
        return _form_page(method, match.group(1), match.group(2), query, form, host)
    if match := _CANCEL.match(path):
        return _cancel_page(method, *match.groups())
    return _not_found()


# ── Helpers ──────────────────────────────────────────────────────────────────

def _throttled(ip, now=None):
    now = time.monotonic() if now is None else now
    if len(_hits) > 5000:
        _hits.clear()  # Bound memory under a spray of addresses.
    window = _hits.setdefault(web_tokens.ip_key(ip), deque())
    while window and window[0] <= now - IP_WINDOW:
        window.popleft()
    if len(window) >= IP_LIMIT:
        return True
    window.append(now)
    return False


def _form(event):
    raw = event.get('body') or ''
    try:
        data = base64.b64decode(raw, validate=True) if event.get('isBase64Encoded') else raw.encode()
    except (binascii.Error, ValueError):
        raise BadRequest from None
    if len(data) > MAX_BODY_BYTES:
        raise BadRequest
    try:
        return {k: v[0] for k, v in parse_qs(data.decode('utf-8', 'replace'), max_num_fields=30).items()}
    except ValueError:
        raise BadRequest from None


def _cached(key, seconds, load):
    now = time.monotonic()
    hit = _cache.get(key)
    if hit and hit[0] > now:
        return hit[1]
    if len(_cache) > 500:
        _cache.clear()
    value = load()
    _cache[key] = (now + seconds, value)
    return value


def _event(sid, token):
    """The event if `token` is its link token, else None (→ 404). The token is
    checked before any database read."""
    if not web_tokens.matches(token, web_tokens.event_token(sid)):
        return None
    session = _cached(('session', sid), SESSION_CACHE_SECONDS, lambda: db.get_session(sid))
    return session if session and session.get('kind') == 'event' else None


def _closed(session):
    try:
        booking_flow.check_open(session)
        return None
    except rentals.BookingError as exc:
        return _resp(200, pages.message('Registration closed', str(exc), session))


def _stock(sid):
    return _cached(('stock', sid), STOCK_CACHE_SECONDS,
                   lambda: rentals.available(booking_flow.state_for(sid)))


def _person_id(sid, submission):
    """Negative (Telegram IDs are positive) and stable per submission."""
    digest = hashlib.sha256(f'{sid}:{submission}'.encode()).hexdigest()
    return -(int(digest[:12], 16) + 1)


def _cancel_url(host, sid, pid):
    return f'https://{host}/c/{sid}/{pid}/{web_tokens.cancel_token(sid, str(pid))}'


def _refresh(sid):
    _cache.pop(('stock', sid), None)
    try:
        booking_flow.refresh_poll(sid)
    except Exception:
        # The registration is saved; the next booking change refreshes the post.
        log.warning('Announcement refresh failed for %s', sid)


# ── Pages ────────────────────────────────────────────────────────────────────

def _event_page(sid, query):
    session = _event(sid, query.get('k'))
    if not session:
        return _not_found()
    if closed := _closed(session):
        return closed
    k = web_tokens.event_token(sid)
    return _resp(200, pages.landing(session, f'/e/{sid}/sit?k={k}', f'/e/{sid}/alumni?k={k}'))


def _form_page(method, sid, category, query, form, host):
    session = _event(sid, query.get('k'))
    if not session:
        return _not_found()
    if closed := _closed(session):
        return closed
    action = f'/e/{sid}/{category}?k={web_tokens.event_token(sid)}'
    if method == 'GET':
        return _render(session, category, action, secrets.token_urlsafe(18), {}, '')
    submission = form.get('sub', '')
    if not _SUBMISSION.fullmatch(submission):
        return _render(session, category, action, secrets.token_urlsafe(18), {}, 'Please fill in the form again.')
    if form.get('website'):
        # Honeypot filled: almost certainly a bot. Look successful, save nothing.
        return _resp(200, pages.message('Thank you', 'Your response was received.', session))
    keep = {k: form.get(k, '') for k in ('name', 'handle', 'programme', 'year', 'skates', 'size',
                                         'upsize', 'guards')}
    try:
        record, handle, rental = _parse(sid, category, form)
    except (ef.FormError, rentals.BookingError) as exc:
        return _render(session, category, action, submission, keep, str(exc))
    pid = _person_id(sid, submission)

    def register(state):
        booking_flow.session_open(sid)  # Re-checked inside the atomic write, as in Telegram.
        return rentals.register_web(state, pid, handle, record, rental=rental)
    try:
        state = store.mutate(sid, register)
    except rentals.BookingError as exc:
        _cache.pop(('stock', sid), None)
        return _render(session, category, action, secrets.token_urlsafe(18), keep, str(exc))
    _refresh(sid)
    person = state['people'][str(pid)]
    return _resp(200, pages.registered(session, _summary(person), _cancel_url(host, sid, pid)))


def _render(session, category, action, submission, values, error):
    status = 400 if error else 200
    if category == 'sit':
        return _resp(status, pages.sit_form(session, action, submission, values, error, _stock(session['id'])))
    return _resp(status, pages.alumni_form(session, action, submission, values, error))


def _parse(sid, category, form):
    name = ef.full_name(form.get('name'))
    handle = ef.telegram_handle(form.get('handle'))
    if category == 'alumni':
        return {'category': 'alumni', 'name': name, 'masked_id': ef.masked_id(form.get('masked_id'))}, handle, None
    student_no = ef.student_number(form.get('student_no'))
    cluster, _, choice = (form.get('programme') or '').partition(':')
    if cluster not in programmes.CLUSTER_ORDER or not choice:
        raise ef.FormError('Please choose your cluster and degree programme.')
    if choice == 'none':
        programme = None
    else:
        try:
            programme = programmes.programme(cluster, int(choice))[0]
        except ValueError:
            raise ef.FormError('Please choose your cluster and degree programme.') from None
    year = ef.year_of_study(form.get('year'))
    skates = form.get('skates') == 'yes'
    record = {'category': 'sit', 'name': name, 'student_no': student_no, 'cluster': cluster,
              'programme': programme, 'year': year, 'skates': skates}
    if not skates:
        return record, handle, None
    try:
        requested = int(form.get('size') or '')
    except ValueError:
        raise ef.FormError('Please choose your EU shoe size for the rental.') from None
    if requested not in rentals.INVENTORY:
        raise ef.FormError('Please choose your EU shoe size for the rental.')
    stock = _stock(sid)  # A hint only; register_web re-checks stock atomically.
    if stock.get(requested, 0) > 0:
        allocated = requested
    elif form.get('upsize') and stock.get(requested + 1, 0) > 0:
        allocated = requested + 1
    elif stock.get(requested + 1, 0) > 0:
        raise ef.FormError(f'EU {requested} has run out, but EU {requested + 1} is available. Tick '
                           '"I accept one EU size larger" to take it, or choose another size.')
    else:
        raise ef.FormError(f'No pair is left in EU {requested} or one size larger. Choose another '
                           'size, or register without skates and ask the committee.')
    return record, handle, {'requested': requested, 'allocated': allocated,
                            'guards': form.get('guards') == '1'}


def _summary(person):
    form = person.get('event_form') or {}
    lines = [('Name', form.get('name')), ('Telegram', person.get('first_name'))]
    if form.get('category') == 'sit':
        lines += [('Category', 'SIT student'), ('Cluster', form.get('cluster')),
                  ('Programme', form.get('programme') or 'Not listed'), ('Year of study', form.get('year'))]
        rental = person.get('rental')
        lines.append(('Rental skates', f"EU {rental['allocated']} (requested EU {rental['requested']}), "
                      f"guards: {'yes' if rental['guards'] else 'no'}" if rental else 'No'))
    else:
        lines.append(('Category', 'SIT alumnus'))
    return lines


def _cancel_page(method, sid, pid, token):
    if not web_tokens.matches(token, web_tokens.cancel_token(sid, pid)):
        return _not_found()  # Checked before any database read.
    session = db.get_session(sid)
    if not session or session.get('kind') != 'event':
        return _not_found()
    person = booking_flow.state_for(sid)['people'].get(pid, {})
    if not person.get('attendance'):
        return _resp(200, pages.message('Not registered', 'This registration was already cancelled.', session))
    if closed := _closed(session):
        return closed
    if method == 'GET':
        return _resp(200, pages.cancel_confirm(session, f'/c/{sid}/{pid}/{token}', person.get('first_name')))
    user = {'id': int(pid), 'first_name': person.get('first_name'), 'username': person.get('username')}
    store.mutate(sid, lambda s: rentals.apply_action(s, user, 'cancel_attendance', int(time.time() * 1000)))
    _refresh(sid)
    return _resp(200, pages.message('Registration cancelled',
                                    'You are no longer registered, and any reserved skates were released.',
                                    session))
