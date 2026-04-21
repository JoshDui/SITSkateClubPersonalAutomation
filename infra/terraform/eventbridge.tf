# ──────────────────────────────────────────────────────────────────────────────
# EventBridge notes:
#
# - The weekly cron RULE (scheduler Lambda trigger) lives in lambda.tf next to
#   the scheduler Lambda that consumes it.
# - The per-session one-off SCHEDULES (EventBridge Scheduler) are created at
#   runtime by the scheduler Lambda via boto3 scheduler.create_schedule().
#   They're not Terraform-managed because their fire times come from DB state.
# - This file only needs to exist if we later add static EventBridge rules
#   (e.g., a daily health-check). Left empty for now intentionally.
# ──────────────────────────────────────────────────────────────────────────────
