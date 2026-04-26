"""
Webhook function — HTTP Trigger.

Receives Telegram webhook POSTs at the Function URL. Verifies the
X-Telegram-Bot-Api-Secret-Token header against Key Vault, then dispatches
button callbacks and admin commands.

TODO: Port from services/webhook/handler.py in milestone A4.
"""
from __future__ import annotations

import azure.functions as func


def main(req: func.HttpRequest) -> func.HttpResponse:
    return func.HttpResponse("not implemented", status_code=501)
