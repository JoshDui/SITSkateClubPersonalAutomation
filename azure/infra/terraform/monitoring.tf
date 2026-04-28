# ──────────────────────────────────────────────────────────────────────────────
# Log Analytics workspace + Application Insights + error alert
#
# App Insights is the workspace-based variant (the legacy classic mode is
# deprecated). All telemetry — function invocations, traces, exceptions,
# httpx outbound calls — flows into Log Analytics, queryable via KQL in
# the portal Logs blade.
#
# Free-tier ingestion: first 5 GB/month per resource. Bot generates <50 MB/mo.
# ──────────────────────────────────────────────────────────────────────────────

resource "azurerm_log_analytics_workspace" "main" {
  name                = local.law_name
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  sku                 = "PerGB2018"
  retention_in_days   = var.log_retention_days
  tags                = local.common_tags
}

resource "azurerm_application_insights" "main" {
  name                = local.ai_name
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  workspace_id        = azurerm_log_analytics_workspace.main.id
  application_type    = "web"
  retention_in_days   = var.log_retention_days
  tags                = local.common_tags
}

# ── Alert: any exception logged by the Function App fires this in 5 min ──

resource "azurerm_monitor_action_group" "default" {
  count               = length(var.budget_alert_emails) > 0 ? 1 : 0
  name                = "${local.prefix}-alerts"
  resource_group_name = azurerm_resource_group.main.name
  short_name          = "skatebot"

  dynamic "email_receiver" {
    for_each = toset(var.budget_alert_emails)
    content {
      name          = replace(email_receiver.value, "@", "-at-")
      email_address = email_receiver.value
    }
  }

  tags = local.common_tags
}

# Scheduled-query alert on the App Insights workspace. Fires when any
# exception is logged in the last 5 minutes — same semantic as AWS-side
# `CloudWatch Alarm on errors metric`.
resource "azurerm_monitor_scheduled_query_rules_alert_v2" "function_errors" {
  count               = length(var.budget_alert_emails) > 0 ? 1 : 0
  name                = "${local.prefix}-function-errors"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location

  evaluation_frequency = "PT5M"
  window_duration      = "PT5M"
  scopes               = [azurerm_log_analytics_workspace.main.id]
  severity             = 2

  criteria {
    query                   = "exceptions | where cloud_RoleName has 'skatebot'"
    time_aggregation_method = "Count"
    threshold               = 0
    operator                = "GreaterThan"

    failing_periods {
      minimum_failing_periods_to_trigger_alert = 1
      number_of_evaluation_periods             = 1
    }
  }

  action {
    action_groups = [azurerm_monitor_action_group.default[0].id]
  }

  tags = local.common_tags
}
