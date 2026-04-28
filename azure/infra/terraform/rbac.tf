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

# The Terraform-running user (Joshua) needs the same data-plane role so the
# local report script (azure/scripts/build_attendance_report.py) can query
# Cosmos through `az login` credentials without admin-side intervention.
# Without this, DefaultAzureCredential gets a token but every read/query
# returns 403.
resource "azurerm_cosmosdb_sql_role_assignment" "tf_cosmos_data_contrib" {
  resource_group_name = azurerm_resource_group.main.name
  account_name        = azurerm_cosmosdb_account.main.name
  role_definition_id  = "${azurerm_cosmosdb_account.main.id}/sqlRoleDefinitions/00000000-0000-0000-0000-000000000002"
  principal_id        = data.azurerm_client_config.current.object_id
  scope               = azurerm_cosmosdb_account.main.id
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

# ── Storage Blob: write attendance CSVs from the exporter Function ────────
#
# Scoped to the storage account (not the single attendance container) so the
# AzureWebJobsStorage path and any future containers are covered without
# revisiting RBAC. Strictly the exporter only needs write to one container —
# tighten to container scope later if least-privilege matters.

resource "azurerm_role_assignment" "fa_blob_data_contrib" {
  scope                = azurerm_storage_account.main.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_linux_function_app.main.identity[0].principal_id
}
