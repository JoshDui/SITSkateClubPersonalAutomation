# ──────────────────────────────────────────────────────────────────────────────
# Cost Management Budget — alerts when actual or forecasted spend crosses
# 50% / 100% of var.budget_amount (default $5).
#
# Skipped entirely when no alert email is configured, on the same theory as
# the AWS-side budget: an unused budget that doesn't email anyone is just
# noise in the portal. Set var.budget_alert_emails to enable.
# ──────────────────────────────────────────────────────────────────────────────

resource "azurerm_consumption_budget_resource_group" "main" {
  count             = length(var.budget_alert_emails) > 0 ? 1 : 0
  name              = "${local.prefix}-budget"
  resource_group_id = azurerm_resource_group.main.id

  amount     = var.budget_amount
  time_grain = "Monthly"

  time_period {
    # Budgets need an explicit start date in YYYY-MM-01 form. Pin to the
    # first day of the current month at apply time; Azure rolls the period
    # forward automatically once it elapses.
    start_date = formatdate("YYYY-MM-01'T'00:00:00'Z'", timestamp())
  }

  notification {
    enabled        = true
    threshold      = 50
    operator       = "GreaterThan"
    threshold_type = "Actual"
    contact_emails = var.budget_alert_emails
  }

  notification {
    enabled        = true
    threshold      = 100
    operator       = "GreaterThan"
    threshold_type = "Actual"
    contact_emails = var.budget_alert_emails
  }

  notification {
    enabled        = true
    threshold      = 100
    operator       = "GreaterThan"
    threshold_type = "Forecasted"
    contact_emails = var.budget_alert_emails
  }

  # `start_date` resolves at plan time. Without ignore, every plan after
  # the first month would diff because timestamp() advances.
  lifecycle {
    ignore_changes = [time_period[0].start_date]
  }
}
