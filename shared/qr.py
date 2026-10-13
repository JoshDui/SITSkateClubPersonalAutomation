"""QR codes for special-event web registration links.

Pure-Python encoding (segno): the link is turned into a QR matrix and a PNG
in memory, in milliseconds. No image service, no storage; the bot sends the
PNG to admins through Telegram. https://segno.readthedocs.io/
"""
from __future__ import annotations

import io

import segno


def png(url: str) -> bytes:
    """A print-friendly PNG: error correction M or better (survives smudges
    on posters), 10 px modules, the standard 4-module quiet zone."""
    code = segno.make(url, error='m', micro=False)
    buf = io.BytesIO()
    code.save(buf, kind='png', scale=10, border=4)
    return buf.getvalue()
