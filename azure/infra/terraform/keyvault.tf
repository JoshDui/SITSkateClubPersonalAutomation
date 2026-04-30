# ──────────────────────────────────────────────────────────────────────────────
# Key Vault + 6 secrets (mirroring the AWS SSM Parameter Store layout)
#
# Authentication mode: RBAC (not legacy access policies). Easier to audit,
# integrates with the Function App's System-Assigned MI via standard role
# assignments (see rbac.tf).
#
# Secret-naming caveat: Key Vault names allow only [0-9 a-z A-Z -]. AWS SSM
# uses underscores; we use lowercase-hyphenated names that the Function
# App's shared/config.py reads with the same kebab-style. See PROGRESS.md
# error #12 for the naming convention discovery.
# ──────────────────────────────────────────────────────────────────────────────

resource "azurerm_key_vault" "main" {
  name                = local.kv_name
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  tenant_id           = data.azurerm_client_config.current.tenant_id

  sku_name = "standard"

  # RBAC authorization, not legacy access policies. Required for Managed
  # Identity to read secrets via Key Vault Secrets User role assignment.
  rbac_authorization_enabled = true

  # 7 days is the minimum and the right choice for free-tier — 90 days
  # (the default) would prevent recreating with the same name for 3 months
  # after a destroy.
  soft_delete_retention_days = 7
  purge_protection_enabled   = false

  tags = local.common_tags
}

# ── Bootstrap RBAC: this Terraform identity needs Secrets Officer to write
# secrets below. Without this, the secret resources fail with 403 Forbidden
# on first apply.

resource "azurerm_role_assignment" "tf_kv_officer" {
  scope                = azurerm_key_vault.main.id
  role_definition_name = "Key Vault Secrets Officer"
  principal_id         = data.azurerm_client_config.current.object_id
}

# ── 6 secrets matching the AWS SSM keys ──────────────────────────────────

resource "azurerm_key_vault_secret" "bot_token" {
  name         = "bot-token"
  value        = var.telegram_bot_token
  key_vault_id = azurerm_key_vault.main.id
  depends_on   = [azurerm_role_assignment.tf_kv_officer]
}

resource "azurerm_key_vault_secret" "admin_ids" {
  name         = "admin-ids"
  value        = var.telegram_admin_ids
  key_vault_id = azurerm_key_vault.main.id
  depends_on   = [azurerm_role_assignment.tf_kv_officer]
}

resource "azurerm_key_vault_secret" "group_chat_id" {
  name         = "group-chat-id"
  value        = var.telegram_group_chat_id
  key_vault_id = azurerm_key_vault.main.id
  depends_on   = [azurerm_role_assignment.tf_kv_officer]
}

resource "azurerm_key_vault_secret" "webhook_secret" {
  name         = "webhook-secret"
  value        = var.telegram_webhook_secret
  key_vault_id = azurerm_key_vault.main.id
  depends_on   = [azurerm_role_assignment.tf_kv_officer]
}

resource "azurerm_key_vault_secret" "power_automate_url" {
  name         = "power-automate-url"
  value        = var.power_automate_url
  key_vault_id = azurerm_key_vault.main.id
  depends_on   = [azurerm_role_assignment.tf_kv_officer]
}

resource "azurerm_key_vault_secret" "rental_skates_handle" {
  name         = "rental-skates-handle"
  value        = var.rental_skates_handle
  key_vault_id = azurerm_key_vault.main.id
  depends_on   = [azurerm_role_assignment.tf_kv_officer]
}

# Optional: target a specific topic in a forum supergroup. Empty value
# (the default) means the bot posts in the 'General' topic.
resource "azurerm_key_vault_secret" "group_topic_id" {
  name         = "group-topic-id"
  value        = var.telegram_group_topic_id
  key_vault_id = azurerm_key_vault.main.id
  depends_on   = [azurerm_role_assignment.tf_kv_officer]
}
