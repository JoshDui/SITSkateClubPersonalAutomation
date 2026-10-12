# ──────────────────────────────────────────────────────────────────────────────
# DynamoDB tables — PROVISIONED capacity inside the Always Free tier.
#
# Always Free covers 25 GB of storage plus 25 provisioned RCU and 25 provisioned
# WCU in total across every table and index. On-demand (PAY_PER_REQUEST) is
# billed per request with no free allowance, so it is not used here.
#   Allocation (RCU / WCU): state 10/10, sessions 4/4, members 3/3,
#   responses 2/2 → 19/19 of 25. Keep the total ≤ 25, and no auto-scaling or
#   point-in-time recovery: both bill beyond the free tier.
# Bursts are absorbed by DynamoDB's burst capacity (~5 minutes of unused
# throughput); a throttled booking tap returns 503 and Telegram retries it.
#
# Access-pattern design notes:
#   members   — point lookup by username (TG handle, lowercased)
#   sessions  — point lookup by id (ULID); listings use a consistent Scan
#               (a few dozen rows a year), so no secondary index is needed
#   responses — query by session_id, optionally filtered to category prefix via
#               composite SK "{category}#{telegram_id}" (BeginsWith-efficient)
#   state     — point lookups by (pk, sk): pk=BOOKING/sk=session_id holds a
#               session's booking document (conditional writes on `rev`),
#               pk=TELEGRAM/sk=user_id a linked profile, pk=CLAIM/sk=username
#               a registry handle claim. See shared/booking_store.py.
# ──────────────────────────────────────────────────────────────────────────────

resource "aws_dynamodb_table" "members" {
  name           = "${local.prefix}-members"
  billing_mode   = "PROVISIONED"
  read_capacity  = 3
  write_capacity = 3
  hash_key       = "username"

  attribute {
    name = "username"
    type = "S"
  }

  point_in_time_recovery {
    enabled = false # PITR is billed per GB-month; not in the free tier
  }
}

resource "aws_dynamodb_table" "sessions" {
  name           = "${local.prefix}-sessions"
  billing_mode   = "PROVISIONED"
  read_capacity  = 4
  write_capacity = 4
  hash_key       = "id"

  attribute {
    name = "id"
    type = "S"
  }
}

resource "aws_dynamodb_table" "responses" {
  name           = "${local.prefix}-responses"
  billing_mode   = "PROVISIONED"
  read_capacity  = 2
  write_capacity = 2
  hash_key       = "session_id"
  range_key      = "sk" # Composite: "{category}#{telegram_id}"

  attribute {
    name = "session_id"
    type = "S"
  }

  attribute {
    name = "sk"
    type = "S"
  }
}

resource "aws_dynamodb_table" "state" {
  name           = "${local.prefix}-state"
  billing_mode   = "PROVISIONED"
  read_capacity  = 10
  write_capacity = 10
  hash_key       = "pk"
  range_key      = "sk"

  attribute {
    name = "pk"
    type = "S"
  }

  attribute {
    name = "sk"
    type = "S"
  }
}
