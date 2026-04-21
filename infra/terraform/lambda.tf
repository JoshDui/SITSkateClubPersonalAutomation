# ──────────────────────────────────────────────────────────────────────────────
# Lambda functions — 3 services, all packaged as ZIP.
#
# Expects these artifacts to exist (built by `make package` or CI):
#   dist/webhook.zip
#   dist/scheduler.zip
#   dist/exporter.zip
#
# If they're missing, `terraform plan` will fail with a clear error. Run
# `make package` from the repo root first.
# ──────────────────────────────────────────────────────────────────────────────

locals {
  dist_dir = "${path.module}/../../dist"
}

# ── Webhook ──────────────────────────────────────────────────────────────────

resource "aws_lambda_function" "webhook" {
  function_name    = "${local.prefix}-webhook"
  role             = aws_iam_role.webhook.arn
  runtime          = var.python_runtime
  handler          = "handler.lambda_handler"
  filename         = "${local.dist_dir}/webhook.zip"
  source_code_hash = filebase64sha256("${local.dist_dir}/webhook.zip")
  timeout          = var.lambda_timeout_seconds
  memory_size      = var.lambda_memory_mb

  environment {
    variables = {
      ENV                   = local.env
      TABLE_MEMBERS         = aws_dynamodb_table.members.name
      TABLE_SESSIONS        = aws_dynamodb_table.sessions.name
      TABLE_RESPONSES       = aws_dynamodb_table.responses.name
      SSM_PREFIX            = "/${local.project}/${local.env}"
      EXPORTER_LAMBDA_ARN   = aws_lambda_function.exporter.arn
      SCHEDULER_TARGET_ROLE = aws_iam_role.scheduler_target.arn
      SCHEDULER_GROUP       = "default"
      SCHEDULE_NAME_PREFIX  = "${local.prefix}-export-"
      TIMEZONE              = var.scheduler_timezone
    }
  }

  depends_on = [aws_cloudwatch_log_group.webhook]
}

resource "aws_lambda_function_url" "webhook" {
  function_name      = aws_lambda_function.webhook.function_name
  authorization_type = "NONE" # Telegram can't sign AWS SigV4; auth is via X-Telegram-Bot-Api-Secret-Token header in handler
  invoke_mode        = "BUFFERED"

  cors {
    allow_origins = ["*"]
    allow_methods = ["POST"]
  }
}

# ── Scheduler ────────────────────────────────────────────────────────────────

resource "aws_lambda_function" "scheduler" {
  function_name    = "${local.prefix}-scheduler"
  role             = aws_iam_role.scheduler.arn
  runtime          = var.python_runtime
  handler          = "handler.lambda_handler"
  filename         = "${local.dist_dir}/scheduler.zip"
  source_code_hash = filebase64sha256("${local.dist_dir}/scheduler.zip")
  timeout          = var.lambda_timeout_seconds
  memory_size      = var.lambda_memory_mb

  environment {
    variables = {
      ENV                   = local.env
      TABLE_MEMBERS         = aws_dynamodb_table.members.name
      TABLE_SESSIONS        = aws_dynamodb_table.sessions.name
      TABLE_RESPONSES       = aws_dynamodb_table.responses.name
      SSM_PREFIX            = "/${local.project}/${local.env}"
      EXPORTER_LAMBDA_ARN   = aws_lambda_function.exporter.arn
      SCHEDULER_TARGET_ROLE = aws_iam_role.scheduler_target.arn
      SCHEDULER_GROUP       = "default"
      SCHEDULE_NAME_PREFIX  = "${local.prefix}-export-"
      TIMEZONE              = var.scheduler_timezone
    }
  }

  depends_on = [aws_cloudwatch_log_group.scheduler]
}

# EventBridge Rule → Scheduler Lambda (weekly cron)
resource "aws_cloudwatch_event_rule" "weekly_poll" {
  name                = "${local.prefix}-weekly-poll"
  description         = "Fires every Sunday 18:00 SGT (10:00 UTC) to trigger the scheduler Lambda."
  schedule_expression = var.weekly_poll_cron_utc
}

resource "aws_cloudwatch_event_target" "weekly_poll_target" {
  rule      = aws_cloudwatch_event_rule.weekly_poll.name
  target_id = "scheduler-lambda"
  arn       = aws_lambda_function.scheduler.arn
}

resource "aws_lambda_permission" "allow_eventbridge_invoke_scheduler" {
  statement_id  = "AllowEventBridgeWeeklyPoll"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.scheduler.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.weekly_poll.arn
}

# ── Exporter ─────────────────────────────────────────────────────────────────

resource "aws_lambda_function" "exporter" {
  function_name    = "${local.prefix}-exporter"
  role             = aws_iam_role.exporter.arn
  runtime          = var.python_runtime
  handler          = "handler.lambda_handler"
  filename         = "${local.dist_dir}/exporter.zip"
  source_code_hash = filebase64sha256("${local.dist_dir}/exporter.zip")
  timeout          = var.lambda_timeout_seconds
  memory_size      = var.lambda_memory_mb

  environment {
    variables = {
      ENV             = local.env
      TABLE_MEMBERS   = aws_dynamodb_table.members.name
      TABLE_SESSIONS  = aws_dynamodb_table.sessions.name
      TABLE_RESPONSES = aws_dynamodb_table.responses.name
      SSM_PREFIX      = "/${local.project}/${local.env}"
    }
  }

  depends_on = [aws_cloudwatch_log_group.exporter]
}

# EventBridge Scheduler invokes this Lambda via the scheduler_target role;
# we still need to grant the role-scoped invoke permission on the function.
resource "aws_lambda_permission" "allow_scheduler_invoke_exporter" {
  statement_id  = "AllowEventBridgeSchedulerInvokeExporter"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.exporter.function_name
  principal     = "scheduler.amazonaws.com"
  source_arn    = "arn:aws:scheduler:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:schedule/default/${local.prefix}-export-*"
}
