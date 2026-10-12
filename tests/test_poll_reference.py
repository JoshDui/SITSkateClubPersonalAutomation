"""Supergroup IDs exceed 32 bits; they must round-trip through DynamoDB as ints."""


def test_supergroup_poll_reference_round_trips_through_dynamodb(db_module):
    db = db_module
    sid = db.create_session('2099-01-01', '18:30', '21:30', 'SIT')
    db.set_poll_message_id(sid, 4901, -1001593198353)
    session = db.get_session(sid)
    assert session['poll_chat_id'] == -1001593198353
    assert type(session['poll_chat_id']) is int
    assert session['poll_message_id'] == 4901


def test_poll_reference_defaults_to_configured_chat(db_module):
    db = db_module
    sid = db.create_session('2099-01-01', '18:30', '21:30', 'SIT')
    db.set_poll_message_id(sid, 7)
    assert db.get_session(sid)['poll_chat_id'] == -100999


def test_route_snapshot_is_stored_on_the_session(db_module):
    db = db_module
    sid = db.create_session('2099-02-05', '18:30', '21:30', 'SIT', group_key='8wheel',
                            group_name='8 Wheel Clan', poll_chat_id=-1002958151210, poll_topic_id=None)
    saved = db.get_session(sid)
    assert saved['poll_chat_id'] == -1002958151210 and type(saved['poll_chat_id']) is int
    assert saved['poll_topic_id'] == 0 and saved['group_key'] == '8wheel'
