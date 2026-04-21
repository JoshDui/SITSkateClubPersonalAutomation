"""
Thin synchronous Telegram Bot API client using httpx.

Why not python-telegram-bot: PTB's Application class is designed for
long-lived processes (polling, internal job queue, context propagation).
In Lambda we just need a handful of HTTP calls per invocation, so a ~60-LOC
wrapper is simpler and has no cold-start cost beyond the httpx import.
"""
from __future__ import annotations

import httpx

from shared import config

_API = "https://api.telegram.org/bot{token}/{method}"
_FILE = "https://api.telegram.org/file/bot{token}/{file_path}"

_DEFAULT_TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=10.0, pool=5.0)


def _call(method: str, **params) -> dict:
    """POST to api.telegram.org/bot<token>/<method>. Raises on transport or
    Telegram-API-level errors; returns the `result` field on success."""
    params = {k: v for k, v in params.items() if v is not None}
    with httpx.Client(timeout=_DEFAULT_TIMEOUT) as client:
        r = client.post(_API.format(token=config.bot_token(), method=method), json=params)
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram {method} failed: {data}")
    return data["result"]


def send_message(chat_id: int, text: str, reply_markup: dict | None = None) -> dict:
    return _call("sendMessage", chat_id=chat_id, text=text, reply_markup=reply_markup)


def edit_message_text(
    chat_id: int,
    message_id: int,
    text: str,
    reply_markup: dict | None = None,
) -> dict | None:
    """Returns the updated Message dict, or None if Telegram responded
    'message is not modified' (non-fatal — this is how Telegram signals
    a no-op edit)."""
    try:
        return _call(
            "editMessageText",
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            reply_markup=reply_markup,
        )
    except RuntimeError as e:
        if "message is not modified" in str(e):
            return None
        raise


def answer_callback_query(callback_query_id: str, text: str = "") -> dict:
    return _call("answerCallbackQuery", callback_query_id=callback_query_id, text=text or None)


def get_file(file_id: str) -> dict:
    return _call("getFile", file_id=file_id)


def download_file(file_path: str, dest: str) -> None:
    """Streams a Telegram-hosted file to the local filesystem."""
    url = _FILE.format(token=config.bot_token(), file_path=file_path)
    with httpx.Client(timeout=_DEFAULT_TIMEOUT) as client, client.stream("GET", url) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_bytes():
                f.write(chunk)


def set_webhook(url: str, secret_token: str, drop_pending_updates: bool = True) -> dict:
    return _call(
        "setWebhook",
        url=url,
        secret_token=secret_token,
        drop_pending_updates=drop_pending_updates,
        allowed_updates=["message", "callback_query"],
    )


def delete_webhook(drop_pending_updates: bool = True) -> dict:
    return _call("deleteWebhook", drop_pending_updates=drop_pending_updates)


def get_webhook_info() -> dict:
    return _call("getWebhookInfo")
