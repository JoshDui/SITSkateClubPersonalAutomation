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

resource "aws_ssm_parameter" "admin_ids" {
  name        = "/${local.project}/${local.env}/admin_ids"
  type        = "String"
  value       = "PLACEHOLDER-comma-separated-telegram-user-ids"
  description = "Comma-separated Telegram numeric user IDs permitted to run admin commands."

  lifecycle {
    ignore_changes = [value]
  }
}

# Not secret: Terraform owns these values (see variables.tf).
resource "aws_ssm_parameter" "group_chat_id" {
  name        = "/${local.project}/${local.env}/group_chat_id"
  type        = "String"
  value       = var.sit_group_chat_id
  description = "Telegram numeric chat ID of the SIT club group (weekly poll destination)."
}

resource "aws_ssm_parameter" "group_topic_id" {
  name        = "/${local.project}/${local.env}/group_topic_id"
  type        = "String"
  value       = var.sit_group_topic_id
  description = "Forum topic (message_thread_id) inside the SIT group where polls are posted."
}

resource "aws_ssm_parameter" "rental_skates_handle" {
  name        = "/${local.project}/${local.env}/rental_skates_handle"
  type        = "String"
  value       = "NotDrivingUnderInfluence"
  description = "Telegram handle (no @) shown in the rental-skates button."
}
