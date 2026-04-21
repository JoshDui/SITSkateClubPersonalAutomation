# ──────────────────────────────────────────────────────────────────────────────
# DynamoDB tables (on-demand billing — free tier: 25 GB + 25 WCU/RCU)
#
# Access-pattern design notes:
#   members   — point lookup by username (TG handle, lowercased)
#   sessions  — point lookup by id (ULID); GSI for "upcoming within N days" range
#   responses — query by session_id, optionally filtered to category prefix via
#               composite SK "{category}#{telegram_id}" (BeginsWith-efficient)
# ──────────────────────────────────────────────────────────────────────────────

resource "aws_dynamodb_table" "members" {
  name         = "${local.prefix}-members"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "username"

  attribute {
    name = "username"
    type = "S"
  }

  point_in_time_recovery {
    enabled = false # Enable if you care about point-in-time restore; costs ~$0.20/GB-month after free tier
  }
}

resource "aws_dynamodb_table" "sessions" {
  name         = "${local.prefix}-sessions"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "id"

  attribute {
    name = "id"
    type = "S"
  }

  # GSI attributes — all items have a static GSI1PK so the GSI acts as a
  # "sort-all-sessions-by-date" index. Hot-partition concern is moot at
  # ~200 sessions/year.
  attribute {
    name = "gsi1_pk"
    type = "S"
  }

  attribute {
    name = "session_date"
    type = "S"
  }

  global_secondary_index {
    name            = "by_date"
    hash_key        = "gsi1_pk"
    range_key       = "session_date"
    projection_type = "ALL"
  }
}

resource "aws_dynamodb_table" "responses" {
  name         = "${local.prefix}-responses"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "session_id"
  range_key    = "sk" # Composite: "{category}#{telegram_id}"

  attribute {
    name = "session_id"
    type = "S"
  }

  attribute {
    name = "sk"
    type = "S"
  }
}
