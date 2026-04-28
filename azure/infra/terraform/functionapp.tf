# ──────────────────────────────────────────────────────────────────────────────
# Function App = App Service Plan (Y1 Consumption Linux) + Storage Account +
# Linux Function App with System-Assigned Managed Identity + export-queue.
#
# Hosting choice: Linux Consumption (Y1) — first 1M executions/mo + 400k GB-s
# free forever. Cold start ~3–5s; acceptable for portfolio scale.
#
# The Storage Account hosts:
#   1. The Function App's runtime artefacts (AzureWebJobsStorage)
#   2. The export-queue Storage Queue used by webhook/scheduler → exporter
# ──────────────────────────────────────────────────────────────────────────────

resource "azurerm_storage_account" "main" {
  name                     = local.sa_name
  resource_group_name      = azurerm_resource_group.main.name
  location                 = azurerm_resource_group.main.location
  account_tier             = "Standard"
  account_replication_type = "LRS" # locally-redundant — cheapest, fine for non-critical state
  account_kind             = "StorageV2"

  # Disable shared-access keys for posterity / tighten later. Currently allowed
  # because `func azure functionapp publish` uses keys for the SCM endpoint.
  shared_access_key_enabled = true

  tags = local.common_tags
}

resource "azurerm_storage_queue" "export" {
  name                 = local.queue_name
  storage_account_name = azurerm_storage_account.main.name
}

# Attendance CSV sink. Replaces the original Power Automate HTTP POST: the
# exporter Function writes one CSV per session (attendance/<date>_<ulid>.csv)
# and the local report script aggregates all CSVs into Attendance.xlsx.
# See azure/PROGRESS.md "Attendance export pivot" — the original Power Automate
# path is dead because its HTTP trigger requires a Premium licence the SIT
# tenant does not grant, and the Microsoft Graph alternative is blocked by
# tenant directory permissions.
resource "azurerm_storage_container" "attendance" {
  name                  = "attendance"
  storage_account_id    = azurerm_storage_account.main.id
  container_access_type = "private"
}

resource "azurerm_service_plan" "main" {
  name                = local.plan_name
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  os_type             = "Linux"
  sku_name            = "Y1" # Consumption plan
  tags                = local.common_tags
}

resource "azurerm_linux_function_app" "main" {
  name                       = local.fa_name
  resource_group_name        = azurerm_resource_group.main.name
  location                   = azurerm_resource_group.main.location
  service_plan_id            = azurerm_service_plan.main.id
  storage_account_name       = azurerm_storage_account.main.name
  storage_account_access_key = azurerm_storage_account.main.primary_access_key

  https_only = true

  identity {
    type = "SystemAssigned"
  }

  site_config {
    application_insights_connection_string = azurerm_application_insights.main.connection_string
    application_insights_key               = azurerm_application_insights.main.instrumentation_key
    ftps_state                             = "Disabled"

    application_stack {
      python_version = var.python_runtime_version
    }
  }

  app_settings = {
    # Functions runtime
    FUNCTIONS_WORKER_RUNTIME = "python"
    AzureWebJobsFeatureFlags = "EnableWorkerIndexing"

    # App-level config — read by shared/config.py on cold start
    ENV               = var.env
    KEY_VAULT_URL     = azurerm_key_vault.main.vault_uri
    COSMOS_ENDPOINT   = azurerm_cosmosdb_account.main.endpoint
    QUEUE_ACCOUNT_URL = "https://${azurerm_storage_account.main.name}.queue.core.windows.net"
    EXPORT_QUEUE_NAME = local.queue_name

    # Blob endpoint for the attendance CSV sink (see azurerm_storage_container.attendance).
    BLOB_ACCOUNT_URL     = "https://${azurerm_storage_account.main.name}.blob.core.windows.net"
    ATTENDANCE_CONTAINER = azurerm_storage_container.attendance.name

    TIMEZONE = var.scheduler_timezone

    TABLE_MEMBERS   = azurerm_cosmosdb_table.members.name
    TABLE_SESSIONS  = azurerm_cosmosdb_table.sessions.name
    TABLE_RESPONSES = azurerm_cosmosdb_table.responses.name

    DEFAULT_SESSION_START    = var.default_session_start
    DEFAULT_SESSION_END      = var.default_session_end
    DEFAULT_SESSION_LOCATION = var.default_session_location

    # Scheduler NCRONTAB — read by host runtime via function.json
    SCHEDULER_CRON = var.scheduler_cron
  }

  tags = local.common_tags
}
