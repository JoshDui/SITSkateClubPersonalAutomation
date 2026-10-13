"""
Runtime configuration for all three Lambda services.

Pattern: plain env vars at import time (zero cost, required fields fail fast);
SSM parameters fetched lazily with module-level caching (one fetch per cold start
per parameter). boto3 SSM client is reused across invocations via warm-container
reuse.

Ported from the Azure Function App (azure/functionapp/shared/config.py): the
public functions and their fallbacks are identical; Key Vault secrets became
SSM parameters under SSM_PREFIX (underscored names, e.g. group_topic_id).
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
TABLE_STATE = os.environ["TABLE_STATE"]
SSM_PREFIX = os.environ.get("SSM_PREFIX", f"/skatebot/{ENV}")

# ── Env vars (optional, with defaults) ──────────────────────────────────────

TIMEZONE = os.environ.get("TIMEZONE", "Asia/Singapore")
BOOKINGS_ENABLED = os.environ.get("BOOKINGS_ENABLED", "false").lower() == "true"
RULES_FORM_URL = os.environ.get("RULES_FORM_URL", "")
BOT_USERNAME = os.environ.get("BOT_USERNAME", "").lstrip("@")
EIGHT_WHEEL_CHAT_ID = os.environ.get('EIGHT_WHEEL_CHAT_ID', '')
# Public web-registration Lambda URL (ends with '/'); empty disables QR links.
WEBFORM_BASE_URL = os.environ.get('WEBFORM_BASE_URL', '')

# EventBridge Scheduler one-off schedules that fire the exporter 24h after a
# session starts. Only the webhook and scheduler Lambdas set these.
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


@functools.cache
def _optional_ssm_param(name: str) -> str | None:
    """Missing parameter → None, so optional settings can fall back. Cached
    separately because functools.cache does not cache the exception, and these
    lookups run on every attendance tap."""
    try:
        return _get_ssm_param(name, decrypt=False)
    except _ssm.exceptions.ParameterNotFound:
        return None


def bot_token() -> str:
    return _get_ssm_param("bot_token")


def webhook_secret() -> str:
    return _get_ssm_param("webhook_secret")


def admin_ids() -> set[int]:
    raw = _get_ssm_param("admin_ids", decrypt=False)
    return {int(x.strip()) for x in raw.split(",") if x.strip()}


def group_chat_id() -> int:
    return int(_get_ssm_param("group_chat_id", decrypt=False))


def attendance_poll_chat_id() -> int:
    """Telegram chat ID used for attendance poll posts.

    Falls back to group_chat_id so a deployment works with only that set.
    """
    raw = (_optional_ssm_param("attendance_poll_chat_id") or "").strip()
    return int(raw) if raw else group_chat_id()


def group_topic_id() -> int | None:
    """Optional: message_thread_id of a topic inside the group's supergroup.
    None (or absent parameter) = post to the default 'General' topic.

    Find the thread_id by right-clicking any message in the target topic in
    Telegram Desktop → Copy Link. The URL is t.me/c/<group>/<thread>/<msg>;
    the thread component is the value to store as 'group_topic_id'."""
    raw = (_optional_ssm_param("group_topic_id") or "").strip()
    return int(raw) if raw else None


def attendance_poll_topic_id() -> int | None:
    """Optional topic/thread ID used for attendance poll posts.

    Falls back to group_topic_id for backwards compatibility.
    """
    raw = _optional_ssm_param("attendance_poll_topic_id")
    if raw is None:
        return group_topic_id()
    raw = raw.strip()
    return int(raw) if raw else group_topic_id()


def rental_skates_handle() -> str:
    return _get_ssm_param("rental_skates_handle", decrypt=False)
