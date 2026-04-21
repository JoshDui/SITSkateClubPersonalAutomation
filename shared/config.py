"""
Runtime configuration for all three Lambda services.

Pattern: plain env vars at import time (zero cost, required fields fail fast);
SSM parameters fetched lazily with module-level caching (one fetch per cold start
per parameter). boto3 SSM client is reused across invocations via warm-container
reuse — single import at the bottom.
"""
from __future__ import annotations

import functools
import os

import boto3

# ── Env vars (required — fail fast on cold start if missing) ────────────────

ENV = os.environ["ENV"]
TABLE_MEMBERS = os.environ["TABLE_MEMBERS"]
TABLE_SESSIONS = os.environ["TABLE_SESSIONS"]
TABLE_RESPONSES = os.environ["TABLE_RESPONSES"]
SSM_PREFIX = os.environ.get("SSM_PREFIX", f"/skatebot/{ENV}")
TIMEZONE = os.environ.get("TIMEZONE", "Asia/Singapore")

# ── Env vars (optional — only some services set these) ──────────────────────

EXPORTER_LAMBDA_ARN = os.environ.get("EXPORTER_LAMBDA_ARN")
SCHEDULER_TARGET_ROLE = os.environ.get("SCHEDULER_TARGET_ROLE")
SCHEDULER_GROUP = os.environ.get("SCHEDULER_GROUP", "default")
SCHEDULE_NAME_PREFIX = os.environ.get("SCHEDULE_NAME_PREFIX", "skatebot-prod-export-")

# ── Defaults the cron uses when auto-creating a session ─────────────────────

DEFAULT_SESSION_START = os.environ.get("DEFAULT_SESSION_START", "18:30")
DEFAULT_SESSION_END = os.environ.get("DEFAULT_SESSION_END", "21:30")
DEFAULT_SESSION_LOCATION = os.environ.get("DEFAULT_SESSION_LOCATION", "SIT @ Punggol Coast")

# ── SSM-backed secrets (lazy + cached per cold-start) ───────────────────────

_ssm = boto3.client("ssm")


@functools.cache
def _get_ssm_param(name: str, decrypt: bool = True) -> str:
    resp = _ssm.get_parameter(Name=f"{SSM_PREFIX}/{name}", WithDecryption=decrypt)
    return resp["Parameter"]["Value"]


def bot_token() -> str:
    return _get_ssm_param("bot_token")


def webhook_secret() -> str:
    return _get_ssm_param("webhook_secret")


def power_automate_url() -> str:
    return _get_ssm_param("power_automate_url")


def admin_ids() -> set[int]:
    raw = _get_ssm_param("admin_ids", decrypt=False)
    return {int(x.strip()) for x in raw.split(",") if x.strip()}


def group_chat_id() -> int:
    return int(_get_ssm_param("group_chat_id", decrypt=False))


def rental_skates_handle() -> str:
    return _get_ssm_param("rental_skates_handle", decrypt=False)
