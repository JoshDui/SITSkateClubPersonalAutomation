"""SSM-backed settings and their fallbacks (moto-mocked Parameter Store)."""
from __future__ import annotations

import importlib

import boto3
import pytest


@pytest.fixture
def load_config(ddb_tables):
    """Reload shared.config against mocked SSM populated with `params`.
    ddb_tables already sets group_chat_id=-100999 and group_topic_id=3041."""
    ssm = boto3.client("ssm")

    def load(params):
        for name, value in params.items():
            ssm.put_parameter(Name=f"/skatebot/test/{name}", Value=value, Type="String", Overwrite=True)
        from shared import config
        return importlib.reload(config)

    return load


def test_poll_destination_defaults_to_group_chat_and_topic(load_config):
    config = load_config({})
    assert config.attendance_poll_chat_id() == -100999
    assert config.attendance_poll_topic_id() == 3041


def test_attendance_poll_destination_overrides_group_destination(load_config):
    config = load_config({"attendance_poll_chat_id": "-100222", "attendance_poll_topic_id": "99"})
    assert config.attendance_poll_chat_id() == -100222
    assert config.attendance_poll_topic_id() == 99


def test_blank_attendance_topic_falls_back_to_group_topic(load_config):
    # SSM rejects empty values; a whitespace-only value is the closest equivalent.
    config = load_config({"attendance_poll_topic_id": " "})
    assert config.attendance_poll_topic_id() == 3041


def test_missing_topics_post_to_general(ddb_tables):
    boto3.client("ssm").delete_parameter(Name="/skatebot/test/group_topic_id")
    from shared import config
    config = importlib.reload(config)
    assert config.group_topic_id() is None
    assert config.attendance_poll_topic_id() is None


def test_admin_ids_and_secrets(load_config):
    config = load_config({"admin_ids": "101, 102,,103"})
    assert config.admin_ids() == {101, 102, 103}
    assert config.bot_token() == "TEST-TOKEN"
    assert config.webhook_secret() == "TEST-SECRET"
