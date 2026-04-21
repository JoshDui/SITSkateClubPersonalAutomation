# ──────────────────────────────────────────────────────────────────────────────
# SSM Parameter Store — Standard tier (free).
#
# Terraform creates the parameter *slots* with placeholder values. Real values
# are populated manually via `aws ssm put-parameter` (see SETUP.md §6) or in
# CI as a bootstrap step. `ignore_changes = [value]` means re-running
# `terraform apply` won't overwrite the real values.
# ──────────────────────────────────────────────────────────────────────────────

resource "aws_ssm_parameter" "bot_token" {
  name  = "/${local.project}/${local.env}/bot_token"
  type  = "SecureString"
  value = "PLACEHOLDER-set-via-aws-ssm-put-parameter"

  lifecycle {
    ignore_changes = [value]
  }
}

resource "aws_ssm_parameter" "webhook_secret" {
  name  = "/${local.project}/${local.env}/webhook_secret"
  type  = "SecureString"
  value = "PLACEHOLDER-set-via-aws-ssm-put-parameter"

  lifecycle {
    ignore_changes = [value]
  }
}

resource "aws_ssm_parameter" "power_automate_url" {
  name  = "/${local.project}/${local.env}/power_automate_url"
  type  = "SecureString"
  value = "PLACEHOLDER-set-via-aws-ssm-put-parameter"

  lifecycle {
    ignore_changes = [value]
  }
}

resource "aws_ssm_parameter" "admin_ids" {
  name        = "/${local.project}/${local.env}/admin_ids"
  type        = "String"
  value       = "PLACEHOLDER-comma-separated-telegram-user-ids"
  description = "Comma-separated Telegram numeric user IDs permitted to run admin commands."

  lifecycle {
    ignore_changes = [value]
  }
}

resource "aws_ssm_parameter" "group_chat_id" {
  name        = "/${local.project}/${local.env}/group_chat_id"
  type        = "String"
  value       = "PLACEHOLDER-telegram-group-chat-id"
  description = "Telegram numeric chat ID of the club group."

  lifecycle {
    ignore_changes = [value]
  }
}

resource "aws_ssm_parameter" "rental_skates_handle" {
  name        = "/${local.project}/${local.env}/rental_skates_handle"
  type        = "String"
  value       = "NotDrivingUnderInfluence"
  description = "Telegram handle (no @) shown in the rental-skates button."
}
