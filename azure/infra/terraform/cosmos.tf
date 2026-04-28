# ──────────────────────────────────────────────────────────────────────────────
# Cosmos DB Table API account (free tier) + 3 tables
#
# Access patterns mirror the AWS DynamoDB design but adapt to Cosmos rules:
#   members   — single partition 'MEMBER', RowKey = lowercased username
#   sessions  — single partition 'SESSION', RowKey = ULID
#   responses — partition by session_id, RowKey = "{category}:{telegram_id}"
#
# Why Table API (not SQL or Mongo): drop-in conceptual map from DynamoDB,
# auto-indexed, free tier covers our entire workload (1000 RU/s + 25 GB).
# Why ':' separator on responses RowKey: '#' is forbidden in Cosmos Table
# API keys (it is in DynamoDB-allowed). See azure/PROGRESS.md error #16.
# ──────────────────────────────────────────────────────────────────────────────

resource "azurerm_cosmosdb_account" "main" {
  name                = local.cosmos_name
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  offer_type          = "Standard"
  kind                = "GlobalDocumentDB"

  # Free tier: 1000 RU/s + 25 GB free forever, one account per subscription.
  # Workload peaks at ~10 RU/s — comfortably within bounds.
  free_tier_enabled = true

  capabilities {
    name = "EnableTable"
  }

  consistency_policy {
    # Session consistency is the default and the cheapest with-guarantees
    # tier. For our single-region single-writer workload, anything stronger
    # adds RU cost without changing observable behaviour.
    consistency_level = "Session"
  }

  geo_location {
    location          = azurerm_resource_group.main.location
    failover_priority = 0
  }

  tags = local.common_tags
}

resource "azurerm_cosmosdb_table" "members" {
  name                = "members"
  resource_group_name = azurerm_resource_group.main.name
  account_name        = azurerm_cosmosdb_account.main.name
  # No throughput block → table inherits the account's shared throughput,
  # which under free tier is 1000 RU/s pooled across all tables. Per-table
  # provisioning would cost $24/month minimum. Only worth it if hot-partitioning.
}

resource "azurerm_cosmosdb_table" "sessions" {
  name                = "sessions"
  resource_group_name = azurerm_resource_group.main.name
  account_name        = azurerm_cosmosdb_account.main.name
}

resource "azurerm_cosmosdb_table" "responses" {
  name                = "responses"
  resource_group_name = azurerm_resource_group.main.name
  account_name        = azurerm_cosmosdb_account.main.name
}
