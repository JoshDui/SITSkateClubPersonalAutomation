"""
Scheduler function — Timer Trigger.

Fires weekly (Sunday 18:00 SGT = 10:00 UTC). Creates a new session in Cosmos DB,
sends the poll to the group, and enqueues an export message with a 24h
visibility timeout for the exporter.

TODO: Port from services/scheduler/handler.py in milestone A5.
"""
from __future__ import annotations

import azure.functions as func


def main(timer: func.TimerRequest) -> None:
    pass
