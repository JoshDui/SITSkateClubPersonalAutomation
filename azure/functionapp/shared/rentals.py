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


def apply_action(state, user, action, update_id, *, version=None,
                 requested=None, allocated=None, guards=False):
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
    elif action == 'review':
        person['review'] = 'Size/eligibility assistance requested'
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
