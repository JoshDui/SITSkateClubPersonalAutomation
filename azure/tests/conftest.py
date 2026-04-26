"""
Pytest fixtures for Azure tests.

For Storage Queue tests: Azurite (local emulator) on port 10001.
For Cosmos DB tests: either the Cosmos DB Emulator (Windows-only) or live
Cosmos DB with table names suffixed `_test`.

TODO: Implement in milestone A3.
"""
