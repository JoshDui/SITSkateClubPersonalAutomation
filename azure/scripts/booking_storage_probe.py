"""Check real Cosmos ETag behavior in one isolated BOOKING row, then remove it.

Requires authorized Azure credentials and app environment settings. This does
not create a SESSION record, Telegram message, attendee, or member profile.
"""
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'functionapp'))
from azure.core import MatchConditions
from azure.core.exceptions import HttpResponseError
from azure.data.tables import UpdateMode
from shared import booking_store, db, rentals


def main():
    key = 'probe-' + uuid.uuid4().hex
    created = False
    try:
        state = booking_store.mutate(key, lambda state: rentals.apply_action(state, {'id': 1}, 'declare_student', 1))
        created = True
        assert state['people']['1']['current_student'] is True
        stale = db._sessions.get_entity(partition_key='BOOKING', row_key=key)
        state = booking_store.mutate(key, lambda state: rentals.apply_action(state, {'id': 1}, 'reserve', 2,
                                      version=1, requested=38, allocated=38))
        assert rentals.available(state)[38] == 0
        try:
            db._sessions.update_entity(dict(stale), mode=UpdateMode.REPLACE,
                                       etag=stale.metadata['etag'], match_condition=MatchConditions.IfNotModified)
        except HttpResponseError as exc:
            assert exc.status_code in (409, 412), (type(exc).__name__, exc.status_code)
            print('Stale write correctly rejected:', type(exc).__name__, exc.status_code)
        else:
            raise AssertionError('Storage accepted a stale write')
        state = booking_store.read(key)
        assert state['people']['1']['rental']['allocated'] == 38
        print('Real Cosmos booking read/write and ETag checks passed.')
    finally:
        if created:
            db._sessions.delete_entity(partition_key='BOOKING', row_key=key)
            print('Isolated diagnostic booking row removed.')


if __name__ == '__main__':
    main()
