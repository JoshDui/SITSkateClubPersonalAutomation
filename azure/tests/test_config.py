from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

FUNCTIONAPP_DIR = Path(__file__).resolve().parents[1] / "functionapp"


def _load_config(monkeypatch, secrets: dict[str, str]):
    class ResourceNotFoundError(Exception):
        pass

    class FakeSecretClient:
        def __init__(self, *args, **kwargs):
            pass

        def get_secret(self, name: str):
            if name not in secrets:
                raise ResourceNotFoundError(name)
            return types.SimpleNamespace(value=secrets[name])

    class FakeDefaultAzureCredential:
        pass

    azure = types.ModuleType("azure")
    azure.__path__ = []
    azure_core = types.ModuleType("azure.core")
    azure_core.__path__ = []
    azure_core_exceptions = types.ModuleType("azure.core.exceptions")
    azure_core_exceptions.ResourceNotFoundError = ResourceNotFoundError
    azure_identity = types.ModuleType("azure.identity")
    azure_identity.DefaultAzureCredential = FakeDefaultAzureCredential
    azure_keyvault = types.ModuleType("azure.keyvault")
    azure_keyvault.__path__ = []
    azure_keyvault_secrets = types.ModuleType("azure.keyvault.secrets")
    azure_keyvault_secrets.SecretClient = FakeSecretClient

    for name, module in {
        "azure": azure,
        "azure.core": azure_core,
        "azure.core.exceptions": azure_core_exceptions,
        "azure.identity": azure_identity,
        "azure.keyvault": azure_keyvault,
        "azure.keyvault.secrets": azure_keyvault_secrets,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)

    monkeypatch.setenv("ENV", "test")
    monkeypatch.setenv("TABLE_MEMBERS", "members")
    monkeypatch.setenv("TABLE_SESSIONS", "sessions")
    monkeypatch.setenv("TABLE_RESPONSES", "responses")
    monkeypatch.setenv("COSMOS_ENDPOINT", "https://cosmos.example.invalid")
    monkeypatch.setenv("KEY_VAULT_URL", "https://vault.example.invalid")
    module_name = "azure_functionapp_shared_config"
    sys.modules.pop(module_name, None)
    spec = importlib.util.spec_from_file_location(
        module_name,
        FUNCTIONAPP_DIR / "shared" / "config.py",
    )
    assert spec is not None and spec.loader is not None
    config = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, module_name, config)
    spec.loader.exec_module(config)
    return config


def test_attendance_poll_topic_empty_secret_falls_back_to_group_topic(monkeypatch):
    config = _load_config(
        monkeypatch,
        {
            "group-chat-id": "-100111",
            "group-topic-id": "42",
            "attendance-poll-chat-id": "-100222",
            "attendance-poll-topic-id": "",
        },
    )

    assert config.attendance_poll_chat_id() == -100222
    assert config.attendance_poll_topic_id() == 42


def test_attendance_poll_topic_missing_secret_falls_back_to_group_topic(monkeypatch):
    config = _load_config(
        monkeypatch,
        {
            "group-chat-id": "-100111",
            "group-topic-id": "42",
            "attendance-poll-chat-id": "-100222",
        },
    )

    assert config.attendance_poll_topic_id() == 42


def test_attendance_poll_destination_overrides_group_destination(monkeypatch):
    config = _load_config(
        monkeypatch,
        {
            "group-chat-id": "-100111",
            "group-topic-id": "42",
            "attendance-poll-chat-id": "-100222",
            "attendance-poll-topic-id": "99",
        },
    )

    assert config.attendance_poll_chat_id() == -100222
    assert config.attendance_poll_topic_id() == 99
