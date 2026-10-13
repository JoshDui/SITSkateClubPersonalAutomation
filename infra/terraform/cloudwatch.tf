# ──────────────────────────────────────────────────────────────────────────────
# CloudWatch Logs — one log group per Lambda with retention.
# CloudWatch Alarm — single "something broke" alarm that pages on any error
# across any of the three Lambdas.
# SNS topic — email subscription for the alarm.
# ──────────────────────────────────────────────────────────────────────────────

resource "aws_cloudwatch_log_group" "webhook" {
  name              = "/aws/lambda/${local.prefix}-webhook"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "scheduler" {
  name              = "/aws/lambda/${local.prefix}-scheduler"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "exporter" {
  name              = "/aws/lambda/${local.prefix}-exporter"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "webform" {
  name              = "/aws/lambda/${local.prefix}-webform"
  retention_in_days = var.log_retention_days
}

# ── Alarm infra ──────────────────────────────────────────────────────────────

resource "aws_sns_topic" "alarms" {
  name = "${local.prefix}-alarms"
}

# Alarm emails only (no AWS Budgets are created by this). SNS email delivery
# is free up to 1,000 notifications a month.
resource "aws_sns_topic_subscription" "alarms_email" {
  count     = var.alarm_email == "" ? 0 : 1
  topic_arn = aws_sns_topic.alarms.arn
  protocol  = "email"
  endpoint  = var.alarm_email
}

resource "aws_cloudwatch_metric_alarm" "lambda_errors" {
  for_each = {
    webhook   = aws_lambda_function.webhook.function_name
    scheduler = aws_lambda_function.scheduler.function_name
    exporter  = aws_lambda_function.exporter.function_name
    webform   = aws_lambda_function.webform.function_name
  }

  alarm_name          = "${local.prefix}-${each.key}-errors"
  alarm_description   = "Any Lambda error in ${each.value} within a 5-minute window."
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = {
    FunctionName = each.value
  }

  alarm_actions = [aws_sns_topic.alarms.arn]
  ok_actions    = [aws_sns_topic.alarms.arn]
}

# The web form is public: warn on unusually heavy daily use (possible abuse)
# well before Lambda's 1M free requests a month are at risk.
resource "aws_cloudwatch_metric_alarm" "webform_traffic" {
  alarm_name          = "${local.prefix}-webform-traffic"
  alarm_description   = "More than ${var.webform_daily_request_alarm} web form requests in a day."
  namespace           = "AWS/Lambda"
  metric_name         = "Invocations"
  statistic           = "Sum"
  period              = 86400
  evaluation_periods  = 1
  threshold           = var.webform_daily_request_alarm
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = {
    FunctionName = aws_lambda_function.webform.function_name
  }

  alarm_actions = [aws_sns_topic.alarms.arn]
}
