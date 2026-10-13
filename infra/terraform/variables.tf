variable "env" {
  description = "Deployment environment name (prod, dev). Drives resource naming."
  type        = string
  default     = "prod"

  validation {
    condition     = contains(["prod", "dev"], var.env)
    error_message = "env must be 'prod' or 'dev'."
  }
}

variable "aws_region" {
  description = "AWS region for all resources. Default ap-southeast-1 (Singapore) minimises Telegram API latency."
  type        = string
  default     = "ap-southeast-1"
}

variable "python_runtime" {
  description = "Lambda Python runtime version."
  type        = string
  default     = "python3.12"
}

variable "lambda_timeout_seconds" {
  description = "Default Lambda execution timeout. Telegram API calls usually complete in <2s; 30s gives plenty of headroom for cold starts + openpyxl imports."
  type        = number
  default     = 30
}

variable "lambda_memory_mb" {
  description = "Lambda memory (MB). Affects both RAM and CPU allocation. 512 MB is a good balance for openpyxl (importmembers) without overpaying on compute."
  type        = number
  default     = 512
}

variable "log_retention_days" {
  description = "CloudWatch Logs retention. 14 days keeps free-tier ingestion well under 5 GB/mo for this low-traffic bot."
  type        = number
  default     = 14
}

variable "budget_alert_email" {
  description = "Email to receive budget alerts at $1/$5/$10 thresholds. Leave empty to skip creating Terraform-managed budgets (you should have set them manually per SETUP.md §2)."
  type        = string
  default     = ""
}

variable "weekly_poll_cron_utc" {
  description = "EventBridge cron expression (UTC) for the weekly poll. Default: Sunday 10:00 UTC = 18:00 SGT."
  type        = string
  default     = "cron(0 10 ? * SUN *)"
}

variable "scheduler_timezone" {
  description = "IANA timezone used by EventBridge Scheduler for per-session one-off export jobs. Must match session times (stored naive as SGT)."
  type        = string
  default     = "Asia/Singapore"
}

variable "bot_username" {
  description = "Telegram bot username (no @). Used for t.me deep links into the rental DM."
  type        = string
  default     = "SITSkateClubBot"
}

variable "rules_form_url" {
  description = "Rules acknowledgement form link shown in the rental DM."
  type        = string
  default     = "https://forms.cloud.microsoft/r/Q9fETRjdMq"
}

variable "eight_wheel_chat_id" {
  description = "8 Wheel Clan supergroup chat ID for manual Thursday polls (/sendpoll 8 Wheel Clan). shared/destinations.py also pins this exact value."
  type        = string
  default     = "-1002958151210"
}

variable "default_session_start" {
  description = "Default session start time (HH:MM, local) for auto-created sessions."
  type        = string
  default     = "18:30"
}

variable "default_session_end" {
  description = "Default session end time (HH:MM, local) for auto-created sessions."
  type        = string
  default     = "21:30"
}

variable "default_session_location" {
  description = "Default location for auto-created sessions."
  type        = string
  default     = "SIT @ Punggol Coast"
}

variable "sit_group_chat_id" {
  description = "SIT Inline Skate supergroup chat ID (from a message link t.me/c/1593198353/...)."
  type        = string
  default     = "-1001593198353"
}

variable "sit_group_topic_id" {
  description = "Topic (message_thread_id) for SIT polls (from t.me/c/<group>/3041/<msg>)."
  type        = string
  default     = "3041"
}

variable "webform_daily_request_alarm" {
  description = "Alarm when the public web form gets more requests than this in a day (club events see far fewer)."
  type        = number
  default     = 5000
}

variable "alarm_email" {
  description = "Email for CloudWatch alarm notifications (Lambda errors, web form traffic). Set in terraform.tfvars (gitignored). Separate from budget_alert_email, which also creates AWS Budgets."
  type        = string
  default     = ""
}
