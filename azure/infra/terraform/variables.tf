variable "env" {
  description = "Deployment environment name (prod, dev). Drives resource naming."
  type        = string
  default     = "prod"

  validation {
    condition     = contains(["prod", "dev"], var.env)
    error_message = "env must be 'prod' or 'dev'."
  }
}

variable "location" {
  description = <<-EOT
    Azure region for all resources. Default japaneast — chosen because the
    Azure-for-Students subscription policy whitelists only:
    eastasia, japaneast, indonesiacentral, koreacentral, japanwest.
    eastasia hit ServiceUnavailable for Cosmos at provisioning time;
    japaneast worked. See azure/PROGRESS.md error #1.
  EOT
  type        = string
  default     = "japaneast"
}

variable "python_runtime_version" {
  description = "Python runtime version for the Function App. Functions Linux Consumption supports 3.9–3.12."
  type        = string
  default     = "3.12"
}

variable "scheduler_cron" {
  description = "NCRONTAB schedule for the weekly poll trigger. Default: Sunday 10:00 UTC = 18:00 SGT."
  type        = string
  default     = "0 0 10 * * 0"
}

variable "scheduler_timezone" {
  description = "IANA timezone the bot uses for session-time arithmetic. Stored as TIMEZONE env var on the Function App."
  type        = string
  default     = "Asia/Singapore"
}

variable "default_session_start" {
  description = "Default session start time (24h format) for /sendpoll if no /setsession override."
  type        = string
  default     = "18:30"
}

variable "default_session_end" {
  description = "Default session end time (24h format)."
  type        = string
  default     = "21:30"
}

variable "default_session_location" {
  description = "Default session venue."
  type        = string
  default     = "SIT @ Punggol Coast"
}

variable "log_retention_days" {
  description = <<-EOT
    Application Insights / Log Analytics retention. 30 days is the free-tier
    default and well within the 5 GB/mo free-tier ingestion cap for this bot.
  EOT
  type        = number
  default     = 30
}

variable "budget_amount" {
  description = "Monthly Cost Management budget threshold in USD. Default $5 — alerts fire at 50% and 100%."
  type        = number
  default     = 5
}

variable "budget_alert_emails" {
  description = "Email addresses that receive Cost Management budget alerts. Empty = skip budget creation."
  type        = list(string)
  default     = []
}

# ── Telegram + Power Automate secrets ──────────────────────────────────────
# These flow into Key Vault. Pass via -var or .tfvars. NEVER commit values.
# A placeholder default keeps `terraform plan` working pre-secret-population;
# the Function App's exporter has a defence-in-depth check for "PLACEHOLDER"
# strings and skips the POST if any secret looks fake.

variable "telegram_bot_token" {
  description = "Telegram bot token from @BotFather. Stored as Key Vault secret 'bot-token'."
  type        = string
  sensitive   = true
  default     = "PLACEHOLDER-bot-token"
}

variable "telegram_admin_ids" {
  description = "Comma-separated list of Telegram user IDs allowed to run admin commands. Stored as Key Vault secret 'admin-ids'."
  type        = string
  sensitive   = true
  default     = "PLACEHOLDER-admin-ids"
}

variable "telegram_group_chat_id" {
  description = "Telegram group chat ID where polls are sent. Stored as Key Vault secret 'group-chat-id'."
  type        = string
  sensitive   = true
  default     = "PLACEHOLDER-group-chat-id"
}

variable "telegram_webhook_secret" {
  description = "Random shared secret Telegram includes in X-Telegram-Bot-Api-Secret-Token header. Stored as Key Vault secret 'webhook-secret'."
  type        = string
  sensitive   = true
  default     = "PLACEHOLDER-webhook-secret"
}

variable "power_automate_url" {
  description = "Power Automate webhook URL the exporter POSTs to. Stored as Key Vault secret 'power-automate-url'."
  type        = string
  sensitive   = true
  default     = "https://example.com/PLACEHOLDER-power-automate"
}

variable "rental_skates_handle" {
  description = "Telegram handle of the rental-skates-coordinator. Stored as Key Vault secret 'rental-skates-handle'."
  type        = string
  sensitive   = true
  default     = "PLACEHOLDER-rental-skates-handle"
}

variable "telegram_group_topic_id" {
  description = <<-EOT
    Optional message_thread_id of a topic inside the group's supergroup. Empty
    string = post in 'General'. Find the thread_id by right-clicking any
    message in the target topic in Telegram Desktop → Copy Link; the URL
    is t.me/c/<group>/<thread>/<msg>; the thread component is this value.
    Stored as Key Vault secret 'group-topic-id'.
  EOT
  type        = string
  sensitive   = true
  default     = ""
}

# ── Override hooks for `terraform import` of existing manual deployment ────
# When importing the resources we already provisioned via az CLI, set these
# to the actual deployed names so Terraform binds to them rather than
# generating fresh random suffixes.

variable "fa_name_override" {
  description = "Function App name override. Set when importing an existing deployment (e.g. 'skatebot-prod-azure-32441')."
  type        = string
  default     = null
}

variable "kv_name_override" {
  description = "Key Vault name override. Set when importing (e.g. 'skatebot-prod-kv-29024')."
  type        = string
  default     = null
}

variable "sa_name_override" {
  description = "Storage Account name override. Set when importing the existing Function App's storage."
  type        = string
  default     = null
}
