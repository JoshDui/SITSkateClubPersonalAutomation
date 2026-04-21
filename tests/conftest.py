"""
Test fixtures.

Uses `moto` to mock AWS services in-process — no Docker / DynamoDB Local
required. Tables are created fresh for each test via the `ddb_tables` fixture.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

# Make `shared` importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(autouse=True)
def aws_env(monkeypatch):
    """Stub env vars that shared.config expects at import time, plus dummy
    AWS credentials so moto doesn't try to hit real AWS."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-southeast-1")
    monkeypatch.setenv("ENV", "test")
    monkeypatch.setenv("TABLE_MEMBERS", "skatebot-test-members")
    monkeypatch.setenv("TABLE_SESSIONS", "skatebot-test-sessions")
    monkeypatch.setenv("TABLE_RESPONSES", "skatebot-test-responses")
    monkeypatch.setenv("SSM_PREFIX", "/skatebot/test")
    monkeypatch.setenv("TIMEZONE", "Asia/Singapore")
    monkeypatch.setenv("DEFAULT_SESSION_START", "18:30")
    monkeypatch.setenv("DEFAULT_SESSION_END", "21:30")
    monkeypatch.setenv("DEFAULT_SESSION_LOCATION", "SIT @ Punggol Coast")


@pytest.fixture
def ddb_tables(aws_env):
    """Spin up mocked DynamoDB + create the 3 tables + yield the resource.
    The mock is torn down at the end of the test."""
    with mock_aws():
        ddb = boto3.resource("dynamodb")

        ddb.create_table(
            TableName="skatebot-test-members",
            KeySchema=[{"AttributeName": "username", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "username", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        ddb.create_table(
            TableName="skatebot-test-sessions",
            KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "id", "AttributeType": "S"},
                {"AttributeName": "gsi1_pk", "AttributeType": "S"},
                {"AttributeName": "session_date", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[{
                "IndexName": "by_date",
                "KeySchema": [
                    {"AttributeName": "gsi1_pk", "KeyType": "HASH"},
                    {"AttributeName": "session_date", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            }],
            BillingMode="PAY_PER_REQUEST",
        )
        ddb.create_table(
            TableName="skatebot-test-responses",
            KeySchema=[
                {"AttributeName": "session_id", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "session_id", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )

        # Populate SSM placeholders that config.py's secret getters need.
        ssm = boto3.client("ssm")
        ssm.put_parameter(Name="/skatebot/test/bot_token", Value="TEST-TOKEN", Type="SecureString")
        ssm.put_parameter(Name="/skatebot/test/webhook_secret", Value="TEST-SECRET", Type="SecureString")
        ssm.put_parameter(Name="/skatebot/test/power_automate_url", Value="https://example.com/pa", Type="SecureString")
        ssm.put_parameter(Name="/skatebot/test/admin_ids", Value="111,222", Type="String")
        ssm.put_parameter(Name="/skatebot/test/group_chat_id", Value="-100999", Type="String")
        ssm.put_parameter(Name="/skatebot/test/rental_skates_handle", Value="NotDrivingUnderInfluence", Type="String")

        yield ddb


@pytest.fixture
def db_module(ddb_tables, monkeypatch):
    """Fresh import of shared.db under the mocked AWS. Reload to reset the
    module-level table resource handles to the mocked ones."""
    import importlib
    from shared import config, db as _db
    importlib.reload(config)
    importlib.reload(_db)
    return _db
