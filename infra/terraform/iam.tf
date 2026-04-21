# ──────────────────────────────────────────────────────────────────────────────
# IAM — one execution role per Lambda + the EventBridge Scheduler target role.
#
# Principle: every role has the minimum policies it needs. No wildcards on
# resources where a specific ARN can be used.
# ──────────────────────────────────────────────────────────────────────────────

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

# ── Shared permission sets ───────────────────────────────────────────────────

data "aws_iam_policy_document" "ddb_rw_all_tables" {
  statement {
    effect = "Allow"
    actions = [
      "dynamodb:GetItem",
      "dynamodb:BatchGetItem",
      "dynamodb:Query",
      "dynamodb:Scan",
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
      "dynamodb:DeleteItem",
      "dynamodb:BatchWriteItem",
    ]
    resources = [
      aws_dynamodb_table.members.arn,
      aws_dynamodb_table.sessions.arn,
      "${aws_dynamodb_table.sessions.arn}/index/*",
      aws_dynamodb_table.responses.arn,
    ]
  }
}

data "aws_iam_policy_document" "ssm_read_params" {
  statement {
    effect  = "Allow"
    actions = ["ssm:GetParameter", "ssm:GetParameters", "ssm:GetParametersByPath"]
    resources = [
      "arn:aws:ssm:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:parameter/${local.project}/${local.env}/*",
    ]
  }

  statement {
    effect    = "Allow"
    actions   = ["kms:Decrypt"]
    resources = ["*"] # SSM SecureString uses aws/ssm default key; narrowing requires looking up its ARN
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["ssm.${data.aws_region.current.name}.amazonaws.com"]
    }
  }
}

# ── Webhook role ─────────────────────────────────────────────────────────────

resource "aws_iam_role" "webhook" {
  name               = "${local.prefix}-webhook-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy_attachment" "webhook_logs" {
  role       = aws_iam_role.webhook.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "webhook_ddb" {
  name   = "ddb-rw"
  role   = aws_iam_role.webhook.id
  policy = data.aws_iam_policy_document.ddb_rw_all_tables.json
}

resource "aws_iam_role_policy" "webhook_ssm" {
  name   = "ssm-read"
  role   = aws_iam_role.webhook.id
  policy = data.aws_iam_policy_document.ssm_read_params.json
}

# ── Scheduler role ───────────────────────────────────────────────────────────

resource "aws_iam_role" "scheduler" {
  name               = "${local.prefix}-scheduler-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy_attachment" "scheduler_logs" {
  role       = aws_iam_role.scheduler.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "scheduler_ddb" {
  name   = "ddb-rw"
  role   = aws_iam_role.scheduler.id
  policy = data.aws_iam_policy_document.ddb_rw_all_tables.json
}

resource "aws_iam_role_policy" "scheduler_ssm" {
  name   = "ssm-read"
  role   = aws_iam_role.scheduler.id
  policy = data.aws_iam_policy_document.ssm_read_params.json
}

# Scheduler Lambda needs to create/delete EventBridge Scheduler schedules
# (one-off DateTriggers for each session's export).
data "aws_iam_policy_document" "eventbridge_scheduler_rw" {
  statement {
    effect = "Allow"
    actions = [
      "scheduler:CreateSchedule",
      "scheduler:DeleteSchedule",
      "scheduler:GetSchedule",
      "scheduler:UpdateSchedule",
      "scheduler:ListSchedules",
    ]
    resources = [
      "arn:aws:scheduler:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:schedule/default/${local.prefix}-export-*",
    ]
  }

  # Needed by scheduler Lambda to pass the scheduler_target role below to
  # EventBridge Scheduler when creating a schedule.
  statement {
    effect    = "Allow"
    actions   = ["iam:PassRole"]
    resources = [aws_iam_role.scheduler_target.arn]
    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["scheduler.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy" "scheduler_eventbridge" {
  name   = "eventbridge-scheduler-rw"
  role   = aws_iam_role.scheduler.id
  policy = data.aws_iam_policy_document.eventbridge_scheduler_rw.json
}

# ── Exporter role ────────────────────────────────────────────────────────────

resource "aws_iam_role" "exporter" {
  name               = "${local.prefix}-exporter-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy_attachment" "exporter_logs" {
  role       = aws_iam_role.exporter.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# Exporter only needs to read sessions+responses and mark sessions exported.
# Give it the full RW for simplicity; tighten in a future pass if desired.
resource "aws_iam_role_policy" "exporter_ddb" {
  name   = "ddb-rw"
  role   = aws_iam_role.exporter.id
  policy = data.aws_iam_policy_document.ddb_rw_all_tables.json
}

resource "aws_iam_role_policy" "exporter_ssm" {
  name   = "ssm-read"
  role   = aws_iam_role.exporter.id
  policy = data.aws_iam_policy_document.ssm_read_params.json
}

# ── EventBridge Scheduler target role ────────────────────────────────────────
# Assumed by the EventBridge Scheduler service when firing one-off schedules;
# grants it permission to invoke the exporter Lambda.

data "aws_iam_policy_document" "scheduler_target_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "scheduler_target" {
  name               = "${local.prefix}-scheduler-target-role"
  assume_role_policy = data.aws_iam_policy_document.scheduler_target_assume.json
}

data "aws_iam_policy_document" "invoke_exporter" {
  statement {
    effect    = "Allow"
    actions   = ["lambda:InvokeFunction"]
    resources = [aws_lambda_function.exporter.arn]
  }
}

resource "aws_iam_role_policy" "scheduler_target_invoke" {
  name   = "invoke-exporter"
  role   = aws_iam_role.scheduler_target.id
  policy = data.aws_iam_policy_document.invoke_exporter.json
}
