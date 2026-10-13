"""Pure session booking rules. Requested shoe size is distinct from allocated stock."""
from __future__ import annotations

from copy import deepcopy

# Registration For SIT Inline Skate.xlsx / Skate inventory!E3:F13 (60 pairs).
INVENTORY = {35: 2, 36: 0, 37: 8, 38: 1, 39: 10, 40: 9,
             41: 9, 42: 6, 43: 6, 44: 5, 45: 4}


class BookingError(ValueError):
    pass


def initial_state(responses=()):
    people = {}
    for response in responses:
        uid = str(response['telegram_id'])
        person = people.setdefault(uid, {'version': 0, 'attendance': None})
        person.update({k: response.get(k) for k in ('username', 'first_name')})
        category = response.get('category')
        if category in ('sit_student', 'non_sit'):
            person['attendance'] = category
        # Old rental taps have no size and must not consume stock or imply eligibility.
    return {'inventory': {str(k): v for k, v in INVENTORY.items()}, 'people': people}


def available(state, user_id=None):
    stock = {int(k): v for k, v in state['inventory'].items()}
    for uid, person in state['people'].items():
        if uid != str(user_id) and person.get('rental'):
            stock[person['rental']['allocated']] -= 1
    return stock


def strip_event_pii(state):
    """Copy of `state` without special-event form answers (names, student
    numbers, masked IDs, year of study). Telegram display names stay so the
    announcement and admin summaries keep working; web registrants' typed
    (unverified) handles are removed too."""
    result = deepcopy(state)
    for person in result['people'].values():
        form = person.pop('event_form', None) or {}
        if form.get('source') == 'web':
            person['first_name'], person['username'] = 'web registrant', None
    return result


def _identity_taken(people, form, exclude=None):
    """Error text if another active registrant has the same student number
    (SIT) or the same name and partial ID (alumni), else None."""
    for key, other in people.items():
        if key == exclude or not other.get('attendance'):
            continue
        other_form = other.get('event_form') or {}
        if form['category'] == 'sit' and other_form.get('student_no') == form.get('student_no'):
            return 'That student number is already registered for this event.'
        if (form['category'] == 'alumni' and other_form.get('masked_id') == form.get('masked_id')
                and other_form.get('name', '').casefold() == form['name'].casefold()):
            return 'You are already registered for this event.'
    return None


EVENT_CAPACITY = 300


def register_web(state, person_id, handle, form, *, rental=None):
    """Add a web-form registrant to a special event (copy of `state`).

    `person_id` is negative (Telegram user IDs are positive) and derived from
    the form's submission ID, so a resubmitted form is idempotent. Refuses a
    handle, student number or alumni identity that is already registered,
    whether it came through Telegram or the web. `rental`, if given, is
    {'requested', 'allocated', 'guards'} and obeys the training rules.
    """
    result = deepcopy(state)
    people = result['people']
    key = str(person_id)
    if key in people:
        if people[key].get('attendance'):
            return result  # Same submission again.
        # Replaying a form after its registration was cancelled must not re-register.
        raise BookingError('This form was already used and then cancelled. Please open the form again.')
    registered = [p for p in people.values() if p.get('attendance')]
    if len(registered) >= EVENT_CAPACITY:
        raise BookingError('This event is full.')
    sit = form['category'] == 'sit'
    if any((p.get('username') or '').lower() == handle for p in registered):
        raise BookingError('That Telegram username is already registered for this event. If it is '
                           'yours, register through the Telegram bot instead.')
    if taken := _identity_taken(people, form):
        raise BookingError(taken)
    person = {'version': 1, 'attendance': 'sit_student' if sit else 'non_sit', 'current_student': sit,
              'first_name': f'@{handle}', 'username': handle, 'last_update': 0,
              'event_form': {**form, 'source': 'web'}}
    if rental:
        if not sit:
            raise BookingError('Rental skates at this event are for SIT students only.')
        requested, allocated = rental['requested'], rental['allocated']
        if requested not in INVENTORY or allocated not in (requested, requested + 1):
            raise BookingError('This size needs admin review.')
        if allocated not in INVENTORY or available(result)[allocated] < 1:
            raise BookingError('That pair is no longer available. Please choose again.')
        person['rental'] = {'requested': requested, 'allocated': allocated, 'guards': bool(rental['guards'])}
    people[key] = person
    return result


def apply_action(state, user, action, update_id, *, version=None,
                 requested=None, allocated=None, guards=False, form=None):
    """Apply one action to a copy, suitable for ETag compare-and-swap retries.

    Telegram retries reuse update_id. Per-user high-water marks prevent duplicate
    toggles, and booking versions prevent old confirmation buttons resurrecting
    cancelled reservations. A failed size change never releases the old pair.
    """
    result = deepcopy(state)
    uid = str(user['id'])
    person = result['people'].setdefault(uid, {'version': 0, 'attendance': None})
    if update_id <= person.get('last_update', -1):
        return result
    if version is not None and version != person['version']:
        raise BookingError('This selection is out of date. Open the size menu again.')
    if action == 'reserve':
        if person.get('current_student') is not True:
            raise BookingError('Please declare that you are a current SIT student for this session.')
        if requested not in INVENTORY or allocated not in (requested, requested + 1):
            raise BookingError('This size needs admin review.')
        if allocated not in INVENTORY or available(result, user['id'])[allocated] < 1:
            raise BookingError('That pair is no longer available. Please choose again.')
        person['rental'] = {'requested': requested, 'allocated': allocated, 'guards': bool(guards)}
        person['attendance'] = 'sit_student'
        person.pop('review', None)
    elif action == 'cancel_rental':
        person.pop('rental', None)
        person.pop('review', None)
    elif action == 'cancel_attendance':
        person.pop('rental', None)
        person.pop('review', None)
        person.pop('event_form', None)  # A withdrawn registrant's details are not kept.
        person['attendance'] = None
    elif action == 'declare_external':
        person['current_student'] = False
        person.pop('rental', None)
        if person.get('attendance'):
            person['attendance'] = 'non_sit'
    elif action == 'declare_student':
        person['current_student'] = True
    elif action in ('sit_student', 'non_sit'):
        if person.get('rental'):
            raise BookingError('Manage your rental in the bot chat before changing attendance.')
        person['attendance'] = None if person.get('attendance') == action else action
        if person['attendance'] == 'non_sit':
            # A newer external declaration supersedes an earlier rental declaration.
            person['current_student'] = False
    elif action == 'review':
        person['review'] = 'Size/eligibility assistance requested'
    elif action == 'event_register':
        # Special events: SIT registrants are current students (rentals allowed);
        # alumni attend with their own skates, so any earlier reservation is released.
        if not form or form.get('category') not in ('sit', 'alumni'):
            raise BookingError('Incomplete registration. Please start again.')
        if taken := _identity_taken(result['people'], form, exclude=uid):
            raise BookingError(taken)
        if form['category'] == 'sit':
            person['attendance'] = 'sit_student'
            person['current_student'] = True
            if not form.get('skates'):
                person.pop('rental', None)
        else:
            person['attendance'] = 'non_sit'
            person['current_student'] = False
            person.pop('rental', None)
        person.pop('review', None)
        person['event_form'] = dict(form)
    else:
        raise BookingError('Unknown booking action.')
    person.update(first_name=(user.get('first_name') or user.get('username') or uid)[:80],
                  username=user.get('username'), last_update=update_id,
                  version=person['version'] + 1)
    return result


def responses(state, session_id):
    items = []
    for uid, person in state['people'].items():
        base = {'session_id': session_id, 'telegram_id': int(uid),
                'username': person.get('username'), 'first_name': person.get('first_name', uid),
                'responded_at': str(person.get('last_update', 0))}
        if person.get('attendance'):
            items.append({**base, 'category': person['attendance']})
        if person.get('rental'):
            items.append({**base, 'category': 'rental_skates', **person['rental']})
    return items
