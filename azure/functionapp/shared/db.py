"""
Cosmos DB Table API access layer.

Mirrors the public function signatures of the AWS shared/db.py so handler
code can be ported without changing call sites.

TODO: Implement in milestone A3 — Cosmos DB + config layer.
Replaces:
    boto3.resource("dynamodb")          → azure.data.tables.TableServiceClient
    boto3.dynamodb.conditions.Key/Attr  → OData filter strings
"""
from __future__ import annotations
