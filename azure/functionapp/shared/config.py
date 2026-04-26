"""
Runtime configuration for the Azure Function App.

Pattern mirrors the AWS shared/config.py: plain env vars at import time
(zero cost, required fields fail fast); Key Vault secrets fetched lazily
with module-level caching (one fetch per cold start per secret). The
SecretClient + DefaultAzureCredential are reused across invocations via
warm-container reuse.

DefaultAzureCredential picks up:
  - Managed Identity in production (Function App's System-Assigned MI)
  - az CLI auth locally (`az login` then run anything)
  - Environment-variable credentials in CI (OIDC federated workload identity)
"""
from __future__ import annotations

import functools
import os

from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient

# ── Env vars (required — fail fast on cold start if missing) ────────────────

ENV = os.environ["ENV"]
TABLE_MEMBERS = os.environ["TABLE_MEMBERS"]
TABLE_SESSIONS = os.environ["TABLE_SESSIONS"]
TABLE_RESPONSES = os.environ["TABLE_RESPONSES"]
COSMOS_ENDPOINT = os.environ["COSMOS_ENDPOINT"]   # e.g. https://skatebot-prod-cosmos.table.cosmos.azure.com:443/
KEY_VAULT_URL = os.environ["KEY_VAULT_URL"]       # e.g. https://skatebot-prod-kv.vault.azure.net/

# ── Env vars (optional, with defaults) ──────────────────────────────────────

TIMEZONE = os.environ.get("TIMEZONE", "Asia/Singapore")

# Storage Queue used by webhook+scheduler to hand off export jobs to exporter.
# Format: https://<storage-account>.queue.core.windows.net/
QUEUE_ACCOUNT_URL = os.environ.get("QUEUE_ACCOUNT_URL")
EXPORT_QUEUE_NAME = os.environ.get("EXPORT_QUEUE_NAME", "export-queue")

# ── Defaults the cron uses when auto-creating a session ─────────────────────

DEFAULT_SESSION_START = os.environ.get("DEFAULT_SESSION_START", "18:30")
DEFAULT_SESSION_END = os.environ.get("DEFAULT_SESSION_END", "21:30")
DEFAULT_SESSION_LOCATION = os.environ.get("DEFAULT_SESSION_LOCATION", "SIT @ Punggol Coast")

# ── Key Vault-backed secrets (lazy + cached per cold-start) ─────────────────

_credential = DefaultAzureCredential()
_secrets = SecretClient(vault_url=KEY_VAULT_URL, credential=_credential)


@functools.cache
def _get_secret(name: str) -> str:
    """Fetch a Key Vault secret by name. Cached per cold start."""
    return _secrets.get_secret(name).value


def bot_token() -> str:
    return _get_secret("bot-token")


def webhook_secret() -> str:
    return _get_secret("webhook-secret")


def power_automate_url() -> str:
    return _get_secret("power-automate-url")


def admin_ids() -> set[int]:
    raw = _get_secret("admin-ids")
    return {int(x.strip()) for x in raw.split(",") if x.strip()}


def group_chat_id() -> int:
    return int(_get_secret("group-chat-id"))


def rental_skates_handle() -> str:
    return _get_secret("rental-skates-handle")
