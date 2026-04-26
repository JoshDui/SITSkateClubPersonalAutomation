"""
Exporter function — Queue Trigger.

Fires 24h after a session start (delivered via Storage Queue with
visibility_timeout=86400). Fetches session + responses from Cosmos DB,
builds the attendance JSON payload, POSTs to the Power Automate URL.

TODO: Port from services/exporter/handler.py in milestone A6.
"""
from __future__ import annotations

import azure.functions as func


def main(msg: func.QueueMessage) -> None:
    pass
