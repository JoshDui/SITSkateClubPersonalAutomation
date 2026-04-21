# ──────────────────────────────────────────────────────────────────────────────
# AWS Budgets — three alarms at $1 / $5 / $10.
#
# Only created when `budget_alert_email` is set. You should ALSO set these
# manually via the Billing console BEFORE the first `terraform apply` — these
# here are a backup/IaC-managed mirror, not the primary defense. See SETUP.md §2.
# ──────────────────────────────────────────────────────────────────────────────

locals {
  budget_thresholds_usd = var.budget_alert_email == "" ? [] : [1, 5, 10]
}

resource "aws_budgets_budget" "cost_alarm" {
  for_each = toset([for t in local.budget_thresholds_usd : tostring(t)])

  name         = "${local.prefix}-alarm-${each.key}usd"
  budget_type  = "COST"
  time_unit    = "MONTHLY"
  limit_amount = each.key
  limit_unit   = "USD"

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.budget_alert_email]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.budget_alert_email]
  }
}
