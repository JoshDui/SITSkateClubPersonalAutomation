# Import existing manually-provisioned Azure resources into Terraform state.
#
# Run this ONCE, after `terraform init`, BEFORE the first `terraform plan`.
# It binds the .tf resources to the live deployment instead of trying to
# recreate them (which would fail on already-exists or — worse — destroy
# data on apply if names happened to differ slightly).
#
# Run from azure/infra/terraform/. Pre-existing resource names are pulled
# straight from `az` queries — they reflect the actual deployment built
# during milestones A1–A6.
#
# Idempotent: re-running after a successful import is a no-op (Terraform
# detects the resource is already in state and skips it).

$ErrorActionPreference = 'Stop'

# ── Resolve actual deployed names from Azure ──────────────────────────────

$RG = "rg-skatebot-prod"
$SUB = az account show --query id -o tsv

Write-Host "Subscription: $SUB"
Write-Host "Resource group: $RG"

$FA       = az functionapp list --resource-group $RG --query "[0].name" -o tsv
$KV       = az keyvault list --resource-group $RG --query "[0].name" -o tsv
$SA       = az storage account list --resource-group $RG --query "[0].name" -o tsv
$COSMOS   = az cosmosdb list --resource-group $RG --query "[0].name" -o tsv
$AI       = az monitor app-insights component show --resource-group $RG --query "[0].name" -o tsv 2>$null
$LAW      = az monitor log-analytics workspace list --resource-group $RG --query "[0].name" -o tsv

if (-not $FA -or -not $KV -or -not $SA -or -not $COSMOS) {
  throw "Could not resolve all expected resource names. Inspect with 'az resource list -g $RG -o table'."
}

Write-Host ""
Write-Host "Resolved resource names:"
Write-Host "  Function App:        $FA"
Write-Host "  Key Vault:           $KV"
Write-Host "  Storage Account:     $SA"
Write-Host "  Cosmos DB account:   $COSMOS"
Write-Host "  App Insights:        $AI"
Write-Host "  Log Analytics:       $LAW"
Write-Host ""

# ── Apply name overrides via -var so Terraform binds to the live names ────
# Persist these into a .auto.tfvars so subsequent `terraform plan` keeps them.

$tfvars = @"
fa_name_override = "$FA"
kv_name_override = "$KV"
sa_name_override = "$SA"
"@
Set-Content -Path "imported.auto.tfvars" -Value $tfvars -Encoding utf8
Write-Host "Wrote imported.auto.tfvars"
Write-Host ""

# ── Resource Group ──
terraform import azurerm_resource_group.main "/subscriptions/$SUB/resourceGroups/$RG"

# ── Cosmos DB account + 3 tables ──
terraform import azurerm_cosmosdb_account.main "/subscriptions/$SUB/resourceGroups/$RG/providers/Microsoft.DocumentDB/databaseAccounts/$COSMOS"
terraform import azurerm_cosmosdb_table.members  "/subscriptions/$SUB/resourceGroups/$RG/providers/Microsoft.DocumentDB/databaseAccounts/$COSMOS/tables/members"
terraform import azurerm_cosmosdb_table.sessions "/subscriptions/$SUB/resourceGroups/$RG/providers/Microsoft.DocumentDB/databaseAccounts/$COSMOS/tables/sessions"
terraform import azurerm_cosmosdb_table.responses "/subscriptions/$SUB/resourceGroups/$RG/providers/Microsoft.DocumentDB/databaseAccounts/$COSMOS/tables/responses"

# ── Key Vault + 6 secrets ──
terraform import azurerm_key_vault.main "/subscriptions/$SUB/resourceGroups/$RG/providers/Microsoft.KeyVault/vaults/$KV"

$KV_URL = az keyvault show --name $KV --query properties.vaultUri -o tsv
foreach ($pair in @(
  @("bot_token",            "bot-token"),
  @("admin_ids",            "admin-ids"),
  @("group_chat_id",        "group-chat-id"),
  @("webhook_secret",       "webhook-secret"),
  @("power_automate_url",   "power-automate-url"),
  @("rental_skates_handle", "rental-skates-handle")
)) {
  $tfres   = $pair[0]
  $kvname  = $pair[1]
  $secver  = az keyvault secret show --vault-name $KV --name $kvname --query id -o tsv
  if ($secver) {
    terraform import "azurerm_key_vault_secret.$tfres" $secver
  } else {
    Write-Warning "Secret $kvname not found in $KV — skipping. Set the corresponding var and `terraform apply` will create it."
  }
}

# ── Storage Account + queue ──
terraform import azurerm_storage_account.main "/subscriptions/$SUB/resourceGroups/$RG/providers/Microsoft.Storage/storageAccounts/$SA"
terraform import azurerm_storage_queue.export "/subscriptions/$SUB/resourceGroups/$RG/providers/Microsoft.Storage/storageAccounts/$SA/queueServices/default/queues/export-queue"

# ── App Service Plan + Function App ──
$PLAN = az functionapp show --name $FA --resource-group $RG --query "appServicePlanId" -o tsv
$PLAN_NAME = ($PLAN -split '/')[-1]
terraform import azurerm_service_plan.main $PLAN
terraform import azurerm_linux_function_app.main "/subscriptions/$SUB/resourceGroups/$RG/providers/Microsoft.Web/sites/$FA"

# ── Log Analytics + Application Insights ──
if ($LAW) {
  terraform import azurerm_log_analytics_workspace.main "/subscriptions/$SUB/resourceGroups/$RG/providers/Microsoft.OperationalInsights/workspaces/$LAW"
}
if ($AI) {
  terraform import azurerm_application_insights.main "/subscriptions/$SUB/resourceGroups/$RG/providers/Microsoft.Insights/components/$AI"
}

Write-Host ""
Write-Host "Import complete. Run 'terraform plan' next:"
Write-Host "  - Diff on app_settings is expected (drift between hand-set + Terraform-set values)."
Write-Host "  - Diff on Key Vault secret values: pass real values via TF_VAR_* envs or .tfvars."
Write-Host "  - Resolve drifts incrementally; the goal is a no-op plan."
