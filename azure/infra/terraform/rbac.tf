# ──────────────────────────────────────────────────────────────────────────────
# Role assignments for the Function App's System-Assigned Managed Identity.
#
# The MI replaces hardcoded credentials. DefaultAzureCredential() in the
# function code picks up the MI token via the IMDS endpoint at runtime;
# no secret material is stored in the function or in env vars.
#
# Three roles are needed:
#   1. Key Vault Secrets User           → shared/config.py reads secrets
#   2. Cosmos DB Built-in Data Contributor → shared/db.py reads/writes tables
#   3. Storage Queue Data Contributor   → webhook/scheduler enqueue export jobs
# ──────────────────────────────────────────────────────────────────────────────

# ── Key Vault: read-only access to secret values ──────────────────────────

resource "azurerm_role_assignment" "fa_kv_secrets_user" {
  scope                = azurerm_key_vault.main.id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_linux_function_app.main.identity[0].principal_id
}

# ── Cosmos DB: data-plane RBAC ────────────────────────────────────────────
#
# The Cosmos DB control-plane uses standard Azure RBAC, but the data plane
# (reading/writing entity rows) uses a separate built-in role definition
# scoped to the Cosmos account itself. The "Cosmos DB Built-in Data
# Contributor" role definition ID is well-known and the same across all
# accounts: 00000000-0000-0000-0000-000000000002.

resource "azurerm_cosmosdb_sql_role_assignment" "fa_cosmos_data_contrib" {
  resource_group_name = azurerm_resource_group.main.name
  account_name        = azurerm_cosmosdb_account.main.name
  # Built-in "Cosmos DB Built-in Data Contributor"
  role_definition_id = "${azurerm_cosmosdb_account.main.id}/sqlRoleDefinitions/00000000-0000-0000-0000-000000000002"
  principal_id       = azurerm_linux_function_app.main.identity[0].principal_id
  scope              = azurerm_cosmosdb_account.main.id
}

# ── Storage Queue: enqueue-only is fine but Contributor includes peek ─────

resource "azurerm_role_assignment" "fa_queue_data_contrib" {
  scope                = azurerm_storage_account.main.id
  role_definition_name = "Storage Queue Data Contributor"
  principal_id         = azurerm_linux_function_app.main.identity[0].principal_id
}

# Storage Queue Data Message Sender / Processor split: the Functions runtime
# itself needs message-processor rights for the queue trigger. In practice
# Storage Queue Data Contributor covers both. If you tighten to least-privilege
# later, the runtime needs the "Processor" role on the queue and the producer
# code needs "Sender".
