"""Secrets for the public special-event web form, all derived by HMAC.

Event links (/e/<sid>?k=…), cancel links (/c/<sid>/<pid>/…) and per-IP
limiter keys are HMACs keyed by the SSM webhook secret. Links are therefore
verified without any database read (a forged or random link costs nothing),
and nothing extra is stored. Rotating the webhook secret invalidates all
outstanding QR codes and cancel links.
"""
from __future__ import annotations

import base64
import hashlib
import hmac

from shared import config


def _mac(message: str) -> bytes:
    return hmac.new(config.webhook_secret().encode(), message.encode(), hashlib.sha256).digest()


def _b64(raw: bytes, length: int) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip('=')[:length]


EVENT_TOKEN_LENGTH = 22  # 132 bits
CANCEL_TOKEN_LENGTH = 32  # 192 bits


def event_token(session_id: str) -> str:
    return _b64(_mac(f'event:{session_id}'), EVENT_TOKEN_LENGTH)


def cancel_token(session_id: str, person_id: str) -> str:
    return _b64(_mac(f'cancel:{session_id}:{person_id}'), CANCEL_TOKEN_LENGTH)


def matches(given: str | None, expected: str) -> bool:
    return hmac.compare_digest((given or '').encode(), expected.encode())


def ip_key(ip: str) -> str:
    """Keyed hash of an IP address for in-memory rate limiting only."""
    return _mac(f'ip:{ip}').hex()[:24]
