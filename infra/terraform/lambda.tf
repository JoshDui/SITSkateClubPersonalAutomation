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

  # Settings every function reads in shared/config.py.
  common_lambda_env = {
    ENV              = local.env
    TABLE_MEMBERS    = aws_dynamodb_table.members.name
    TABLE_SESSIONS   = aws_dynamodb_table.sessions.name
    TABLE_RESPONSES  = aws_dynamodb_table.responses.name
    TABLE_STATE      = aws_dynamodb_table.state.name
    SSM_PREFIX       = "/${local.project}/${local.env}"
    TIMEZONE         = var.scheduler_timezone
    BOOKINGS_ENABLED = "true"
    BOT_USERNAME     = var.bot_username
    RULES_FORM_URL   = var.rules_form_url
    # Explicit manual-only Thursday destination; never replaces SIT routing.
    EIGHT_WHEEL_CHAT_ID      = var.eight_wheel_chat_id
    DEFAULT_SESSION_START    = var.default_session_start
    DEFAULT_SESSION_END      = var.default_session_end
    DEFAULT_SESSION_LOCATION = var.default_session_location
  }

  # Functions that create export schedules (webhook /sendpoll, /setsession; scheduler).
  export_schedule_env = {
    EXPORTER_LAMBDA_ARN   = aws_lambda_function.exporter.arn
    SCHEDULER_TARGET_ROLE = aws_iam_role.scheduler_target.arn
    SCHEDULER_GROUP       = "default"
    SCHEDULE_NAME_PREFIX  = "${local.prefix}-export-"
  }
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
    variables = merge(local.common_lambda_env, local.export_schedule_env, {
      # Public sign-up links in the /eventqr QR codes.
      WEBFORM_BASE_URL = aws_lambda_function_url.webform.function_url
    })
  }

  depends_on = [aws_cloudwatch_log_group.webhook]
}

# Public access needs TWO resource-policy statements (required since Oct 2025):
#   lambda:InvokeFunctionUrl  (Principal *, lambda:FunctionUrlAuthType = NONE)
#   lambda:InvokeFunction     (Principal *, lambda:InvokedViaFunctionUrl = true)
# AWS provider 5.x cannot express the second condition, so both statements are
# managed outside Terraform; see "Function URL access" in SETUP.md. Without
# them the URL answers 403 before the handler runs and Telegram gets nothing.
# https://docs.aws.amazon.com/lambda/latest/dg/urls-auth.html
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
    variables = merge(local.common_lambda_env, local.export_schedule_env)
  }

  depends_on = [aws_cloudwatch_log_group.scheduler]
}

# EventBridge Rule → Scheduler Lambda (weekly cron)
resource "aws_cloudwatch_event_rule" "weekly_poll" {
  name                = "${local.prefix}-weekly-poll"
  description         = "Fires every Sunday 18:00 SGT (10:00 UTC) to trigger the scheduler Lambda."
  schedule_expression = var.weekly_poll_cron_utc
  # Explicit, so a rule disabled outside Terraform shows up as drift and is
  # re-enabled on apply (an unset state is not compared).
  state = "ENABLED"
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
  # One Telegram upload per admin (5 × up to 10s read timeout) must fit.
  timeout     = 60
  memory_size = var.lambda_memory_mb

  environment {
    variables = local.common_lambda_env
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

# ── Web form (special-event registration via QR code) ────────────────────────
# Public pages served by Lambda. Event and cancel links are HMAC tokens checked
# before any database access, requests are rate limited per IP in memory, and
# page data is cached per container, so junk traffic does not consume the
# DynamoDB capacity the Telegram bot shares. The account's concurrency limit
# is 10, which leaves no room to reserve concurrency for it; kill switch if
# it is ever flooded (stops it instantly, free):
#   aws lambda put-function-concurrency --function-name skatebot-prod-webform \
#     --reserved-concurrent-executions 0

resource "aws_lambda_function" "webform" {
  function_name    = "${local.prefix}-webform"
  role             = aws_iam_role.webform.arn
  runtime          = var.python_runtime
  handler          = "handler.lambda_handler"
  filename         = "${local.dist_dir}/webform.zip"
  source_code_hash = filebase64sha256("${local.dist_dir}/webform.zip")
  timeout          = 15
  memory_size      = 256

  environment {
    variables = local.common_lambda_env
  }

  depends_on = [aws_cloudwatch_log_group.webform]
}

# Public (AuthType NONE) like the webhook: needs the same two resource-policy
# statements, added outside Terraform (SETUP.md "Function URL access").
resource "aws_lambda_function_url" "webform" {
  function_name      = aws_lambda_function.webform.function_name
  authorization_type = "NONE"
  invoke_mode        = "BUFFERED"
}
