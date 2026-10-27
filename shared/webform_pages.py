"""HTML for the special-event web registration form (services/webform).

Plain server-rendered HTML with inline CSS and no JavaScript, so the page
can be served under a strict Content-Security-Policy. Every value written
into the markup goes through `esc` (html.escape).

Student numbers and partial NRIC/FIN values are never written back into a
page (not even after a validation error); the person re-types them.
"""
from __future__ import annotations

from html import escape

from shared import event_forms as ef
from shared import poll, programmes, rentals

_CSS = """
*{box-sizing:border-box}body{margin:0;font:16px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
background:#f4f5f7;color:#1c1e21}main{max-width:560px;margin:0 auto;padding:20px 16px 48px}
h1{font-size:1.4rem;margin:.2em 0}h2{font-size:1.05rem;margin:1.4em 0 .4em}
.card{background:#fff;border-radius:12px;padding:16px;margin:12px 0;box-shadow:0 1px 3px rgba(0,0,0,.08)}
.meta{color:#555;margin:.2em 0}.details{white-space:pre-wrap;margin-top:.8em}
label{display:block;font-weight:600;margin:14px 0 4px}.hint{font-weight:400;color:#666;font-size:.9rem}
input[type=text],select{width:100%;padding:11px 12px;font-size:16px;border:1px solid #c8ccd2;border-radius:8px;background:#fff}
.choice{display:flex;gap:8px;align-items:flex-start;font-weight:400;margin:8px 0}
.choice input{margin-top:5px}button,.button{display:block;width:100%;padding:13px;margin:14px 0 0;font-size:16px;
font-weight:700;border:0;border-radius:10px;background:#4b5bd6;color:#fff;text-align:center;text-decoration:none}
.secondary{background:#e7e9f6;color:#2b3270}.danger{background:#c62828}
.error{background:#fdecea;color:#8a1c14;border-radius:8px;padding:10px 12px;margin:12px 0}
.ok{background:#e8f5e9;color:#1b5e20;border-radius:8px;padding:10px 12px;margin:12px 0}
.hp{position:absolute;left:-10000px;width:1px;height:1px;overflow:hidden}
dl{margin:0}dt{color:#666;font-size:.9rem}dd{margin:0 0 8px;font-weight:600}
code{word-break:break-all;background:#f0f1f4;padding:2px 4px;border-radius:4px}
"""


def esc(value):
    return escape(str(value if value is not None else ''), quote=True)


def page(title, body):
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<meta name="robots" content="noindex"><title>{esc(title)}</title>'
            f'<style>{_CSS}</style></head><body><main>{body}</main></body></html>')


def event_card(session):
    details = session.get('details')
    return ('<div class="card">'
            f"<h1>{esc(session.get('title') or 'Special event')}</h1>"
            f"<p class=\"meta\">{esc(poll._format_date(session['session_date']))}, "
            f"{esc(poll._format_time(session['start_time']))} – {esc(poll._format_time(session['end_time']))}</p>"
            f"<p class=\"meta\">{esc(session['location'])}</p>"
            + (f'<p class="details">{esc(details)}</p>' if details else '')
            + '</div>')


def landing(session, sit_url, alumni_url):
    return page(session.get('title') or 'Special event', event_card(session) + (
        '<div class="card"><h2>Register</h2>'
        '<p>Club committee members use your details for attendance (and campus security for alumni and external participants). '
        'They are deleted from the bot after the event.</p>'
        f'<a class="button" href="{esc(sit_url)}">I am a current SIT student</a>'
        f'<a class="button secondary" href="{esc(alumni_url)}">I am an SIT alumnus or external participant</a></div>'))


def _error(message):
    return f'<div class="error" role="alert">{esc(message)}</div>' if message else ''


def _text(name, label, value='', hint='', **attrs):
    extra = ''.join(f' {k}="{esc(v)}"' for k, v in attrs.items())
    hint_html = f' <span class="hint">{esc(hint)}</span>' if hint else ''
    return (f'<label for="{name}">{esc(label)}{hint_html}</label>'
            f'<input type="text" id="{name}" name="{name}" value="{esc(value)}"{extra}>')


def _common_hidden(submission):
    return (f'<input type="hidden" name="sub" value="{esc(submission)}">'
            '<div class="hp" aria-hidden="true"><label for="website">Leave empty</label>'
            '<input type="text" id="website" name="website" tabindex="-1" autocomplete="off"></div>')


def _programme_select(selected):
    parts = ['<label for="programme">Cluster and degree programme</label>'
             '<select id="programme" name="programme" required><option value="">Choose…</option>']
    for cluster in programmes.CLUSTER_ORDER:
        parts.append(f'<optgroup label="{esc(cluster)}">')
        for index, (_, label) in enumerate(programmes.PROGRAMMES[cluster]):
            value = f'{cluster}:{index}'
            sel = ' selected' if value == selected else ''
            parts.append(f'<option value="{esc(value)}"{sel}>{esc(label)}</option>')
        value = f'{cluster}:none'
        sel = ' selected' if value == selected else ''
        parts.append(f'<option value="{esc(value)}"{sel}>Not listed ({esc(cluster)})</option></optgroup>')
    parts.append('</select>')
    return ''.join(parts)


def _year_select(selected):
    options = ''.join(f'<option value="{esc(k)}"{" selected" if k == selected else ""}>{esc(v)}</option>'
                      for k, v in ef.YEARS.items())
    return ('<label for="year">Year of study</label><select id="year" name="year" required>'
            f'<option value="">Choose…</option>{options}</select>')


def _size_select(stock, selected):
    options = []
    for size in rentals.INVENTORY:
        left = stock.get(size, 0)
        note = f'{left} left' if left else 'none left'
        sel = ' selected' if str(size) == selected else ''
        options.append(f'<option value="{size}"{sel}>EU {size} · {note}</option>')
    return ('<label for="size">Your normal EU shoe size <span class="hint">(only if renting)</span></label>'
            f'<select id="size" name="size"><option value="">Choose…</option>{"".join(options)}</select>')


def _check(name, label, checked):
    return (f'<label class="choice"><input type="checkbox" name="{name}" value="1"'
            f'{" checked" if checked else ""}> <span>{esc(label)}</span></label>')


def sit_form(session, action, submission, values, error, stock):
    v = values
    skates = v.get('skates', 'no')
    body = event_card(session) + '<div class="card"><h2>Register as an SIT student</h2>' + _error(error) + (
        f'<form method="post" action="{esc(action)}">{_common_hidden(submission)}'
        + _text('name', 'Full name', v.get('name', ''), autocomplete='name', maxlength=80, required='')
        + _text('student_no', 'SIT student number', '', '7 digits', inputmode='numeric',
                autocomplete='off', maxlength=10, required='')
        + _text('handle', 'Telegram username', v.get('handle', ''), 'shown on the group announcement',
                autocomplete='off', maxlength=33, placeholder='@username', required='')
        + _programme_select(v.get('programme', '')) + _year_select(v.get('year', ''))
        + '<label>Do you need to rent skates?</label>'
        + f'<label class="choice"><input type="radio" name="skates" value="no"{" checked" if skates != "yes" else ""}> '
          '<span>No, I have my own</span></label>'
        + f'<label class="choice"><input type="radio" name="skates" value="yes"{" checked" if skates == "yes" else ""}> '
          '<span>Yes, I need rental skates</span></label>'
        + _size_select(stock, v.get('size', ''))
        + _check('upsize', 'If my size has run out, I accept one EU size larger', v.get('upsize'))
        + _check('guards', 'I also need guards', v.get('guards'))
        + '<button type="submit">Review and register</button></form></div>')
    return page('Register: SIT student', body)


def alumni_form(session, action, submission, values, error):
    v = values
    body = event_card(session) + '<div class="card"><h2>Register as an alumnus or external participant</h2>' + _error(error) + (
        f'<form method="post" action="{esc(action)}">{_common_hidden(submission)}'
        + _text('name', 'Full name', v.get('name', ''), autocomplete='name', maxlength=80, required='')
        + _text('masked_id', 'Partial NRIC/FIN for campus security', '',
                'first letter, last 3 digits and last letter only, e.g. T567D — never the full number',
                autocomplete='off', maxlength=12, required='')
        + _text('handle', 'Telegram username', v.get('handle', ''), 'shown on the group announcement',
                autocomplete='off', maxlength=33, placeholder='@username', required='')
        + '<button type="submit">Register</button></form></div>')
    return page('Register: Alumni / External', body)


def registered(session, lines, cancel_url):
    rows = ''.join(f'<dt>{esc(k)}</dt><dd>{esc(v)}</dd>' for k, v in lines)
    return page('Registered', event_card(session) + (
        '<div class="card"><div class="ok">You are registered. You will appear on the club announcement.</div>'
        f'<dl>{rows}</dl><h2>Need to cancel?</h2>'
        '<p>Save or screenshot this private link. Anyone with it can cancel your registration.</p>'
        f'<p><a href="{esc(cancel_url)}">Cancel my registration</a></p>'
        f'<p><code>{esc(cancel_url)}</code></p></div>'))


def cancel_confirm(session, action, who):
    return page('Cancel registration', event_card(session) + (
        f'<div class="card"><h2>Cancel the registration for {esc(who)}?</h2>'
        '<p>Any rental skates you reserved will be released.</p>'
        f'<form method="post" action="{esc(action)}"><button class="danger" type="submit">'
        'Yes, cancel my registration</button></form></div>'))


def message(title, text, session=None):
    card = event_card(session) if session else ''
    return page(title, card + f'<div class="card"><h2>{esc(title)}</h2><p>{esc(text)}</p></div>')
