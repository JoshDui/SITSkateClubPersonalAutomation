from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

FUNCTIONAPP_DIR = Path(__file__).resolve().parents[1] / "functionapp"


def _load_webhook(monkeypatch):
    calls = {
        "answers": [],
        "edits": [],
        "toggles": [],
    }

    config = types.ModuleType("shared.config")
    config.TIMEZONE = "Asia/Singapore"
    config.DEFAULT_SESSION_START = "18:30"
    config.DEFAULT_SESSION_END = "21:30"
    config.DEFAULT_SESSION_LOCATION = "SIT @ Punggol Coast"
    config.QUEUE_ACCOUNT_URL = "https://queue.example.invalid"
    config.EXPORT_QUEUE_NAME = "export-queue"
    config.webhook_secret = lambda: "secret"
    config.admin_ids = lambda: {123}
    config.attendance_poll_chat_id = lambda: -100222
    config.attendance_poll_topic_id = lambda: 99

    poll = types.ModuleType("shared.poll")
    poll.CATEGORY_KEYS = ("sit_student", "non_sit", "rental_skates")
    poll.build_poll_text = lambda session, responses: "poll text"
    poll.build_keyboard = lambda session_id: {"inline_keyboard": []}

    db = types.ModuleType("shared.db")
    db.toggle_response = lambda **kwargs: calls["toggles"].append(kwargs) or True
    db.get_session = lambda session_id: {
        "id": session_id,
        "session_date": "2026-06-02",
        "start_time": "18:30",
        "end_time": "21:30",
        "location": "SIT @ Punggol Coast",
    }
    db.get_responses = lambda session_id: []

    telegram = types.ModuleType("shared.telegram")
    telegram.answer_callback_query = (
        lambda callback_query_id, text="": calls["answers"].append((callback_query_id, text))
    )
    telegram.edit_message_text = lambda **kwargs: calls["edits"].append(kwargs)

    shared = types.ModuleType("shared")
    shared.config = config
    shared.db = db
    shared.importer = types.ModuleType("shared.importer")
    shared.poll = poll
    shared.telegram = telegram

    azure = types.ModuleType("azure")
    azure.__path__ = []
    azure_functions = types.ModuleType("azure.functions")
    azure_identity = types.ModuleType("azure.identity")
    azure_identity.DefaultAzureCredential = type("DefaultAzureCredential", (), {})
    azure_storage = types.ModuleType("azure.storage")
    azure_storage.__path__ = []
    azure_storage_queue = types.ModuleType("azure.storage.queue")
    azure_storage_queue.QueueClient = type("QueueClient", (), {})
    azure_storage_queue.TextBase64EncodePolicy = type("TextBase64EncodePolicy", (), {})

    for name, module in {
        "shared": shared,
        "shared.config": config,
        "shared.db": db,
        "shared.importer": shared.importer,
        "shared.poll": poll,
        "shared.telegram": telegram,
        "azure": azure,
        "azure.functions": azure_functions,
        "azure.identity": azure_identity,
        "azure.storage": azure_storage,
        "azure.storage.queue": azure_storage_queue,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)

    monkeypatch.syspath_prepend(str(FUNCTIONAPP_DIR))
    sys.modules.pop("webhook", None)
    return importlib.import_module("webhook"), calls


def test_callback_accepts_user_not_present_in_member_database(monkeypatch):
    webhook, calls = _load_webhook(monkeypatch)
    session_id = "01KSHRP2YPVJX1RBJ1DPJB8X8Z"

    webhook._handle_callback(
        {
            "id": "callback-1",
            "data": f"vote:{session_id}:sit_student",
            "from": {"id": 987654321, "first_name": "New Member"},
            "message": {"chat": {"id": -100222}, "message_id": 777},
        }
    )

    assert calls["toggles"] == [
        {
            "session_id": session_id,
            "telegram_id": 987654321,
            "username": None,
            "first_name": "New Member",
            "category": "sit_student",
            "responded_at": calls["toggles"][0]["responded_at"],
        }
    ]
    assert calls["edits"] == [
        {
            "chat_id": -100222,
            "message_id": 777,
            "text": "poll text",
            "reply_markup": {"inline_keyboard": []},
        }
    ]
    assert calls["answers"] == [("callback-1", "Added.")]


def test_legacy_callback_does_not_write_deleted_session(monkeypatch):
    webhook, calls = _load_webhook(monkeypatch)
    webhook.db.get_session = lambda sid: None
    webhook._handle_callback({
        'id': 'callback-1', 'data': 'vote:01KSHRP2YPVJX1RBJ1DPJB8X8Z:sit_student',
        'from': {'id': 987654321, 'first_name': 'Test'},
        'message': {'chat': {'id': -100222}, 'message_id': 777},
    })
    assert not calls['toggles']
    assert not calls['edits']


def test_admin_session_commands_dispatch_and_reject_nonadmins(monkeypatch):
    webhook, calls = _load_webhook(monkeypatch)
    calls['commands'] = []
    webhook.telegram.send_message = lambda *args, **kwargs: None
    session_admin = types.SimpleNamespace(
        list_sessions=lambda msg, args: calls['commands'].append(('sessions', args)),
        delete_session=lambda msg, args: calls['commands'].append(('delete', args)))
    monkeypatch.setattr(sys.modules['shared'], 'session_admin', session_admin, raising=False)
    webhook._handle_message({'from': {'id': 123}, 'chat': {'id': 123, 'type': 'private'}, 'text': '/sessions'})
    webhook._handle_message({'from': {'id': 123}, 'chat': {'id': 123, 'type': 'private'},
                             'text': '/deletesession 01KSHRP2YPVJX1RBJ1DPJB8X8Z'})
    webhook._handle_message({'from': {'id': 999}, 'chat': {'id': 999, 'type': 'private'}, 'text': '/sessions'})
    assert calls['commands'] == [('sessions', []), ('delete', ['01KSHRP2YPVJX1RBJ1DPJB8X8Z'])]
