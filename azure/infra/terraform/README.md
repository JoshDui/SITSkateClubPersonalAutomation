# Azure Terraform (azurerm provider)

Mirrors the AWS Terraform stack at `../../../infra/terraform/` for a parallel multi-cloud deployment.

## Files

| File | Purpose |
|---|---|
| `main.tf` | Resource Group + locals (project, env, naming) + random suffixes for globally-unique names |
| `providers.tf` | azurerm provider config + bootstrap data sources |
| `variables.tf` | Inputs: env, location, scheduler cron, secret values, name-override hooks for import |
| `cosmos.tf` | Cosmos DB Table API account (free tier) + 3 tables |
| `keyvault.tf` | Key Vault (RBAC mode) + 6 secrets matching AWS SSM names |
| `functionapp.tf` | Storage Account + App Service Plan (Y1) + Linux Function App (System-Assigned MI) + export-queue |
| `monitoring.tf` | Log Analytics workspace + App Insights + scheduled-query alert on exceptions |
| `rbac.tf` | Role assignments for the Function App's MI: Key Vault Secrets User, Cosmos Data Contributor, Storage Queue Data Contributor |
| `budget.tf` | Cost Management Budget at $5 with email alerts (skipped if no email) |
| `outputs.tf` | webhook URL, function app name, vault name, etc. |
| `import.ps1` | Imports the existing manually-provisioned deployment into Terraform state — run once before first `apply` |

## Usage

### Importing the existing deployment (you are here)

The deployment was built imperatively via `az` CLI during milestones A1–A6. To bind Terraform to it without recreating:

```powershell
cd azure/infra/terraform
terraform init
.\import.ps1
terraform plan        # inspect drift
# edit .tf files / .tfvars to converge
terraform plan        # iterate until plan is a no-op
```

The import script writes `imported.auto.tfvars` with the actual deployed names so subsequent runs stay bound to them.

### Fresh deployment (post-import, future tear-down + recreate)

```powershell
cd azure/infra/terraform
terraform init
terraform apply -var="telegram_bot_token=..." -var="telegram_admin_ids=..." -var=...
```

Or with a `.tfvars` file (gitignored):
```powershell
terraform apply -var-file=secrets.tfvars
```

After apply:
```powershell
$FA = (terraform output -raw function_app_name)
cd ../../functionapp
func azure functionapp publish $FA --python --build remote
```

## Drift you can expect on first plan

The deployment was hand-built with `az` defaults. Terraform will show drift on:

1. **Tags** — manually-provisioned resources lack the `ManagedBy=terraform` and `Cloud=azure` tags. Apply once to add them.
2. **App settings** — the Function App has settings written by `az functionapp config appsettings set` that aren't in `functionapp.tf`'s `app_settings` block. Either add them to the .tf or accept the diff.
3. **Key Vault secret values** — Terraform plans show secret value diff if the var defaults are still placeholders. Set real values via `TF_VAR_*` env vars or `secrets.tfvars`.
4. **App Insights `local_authentication_disabled`** — recent default changed; harmless re-set.
5. **`https_only`, `ftps_state`** — Terraform imposes secure defaults; manually-provisioned deployments may have permissive ones. Apply once.

## Teardown

```powershell
cd azure/infra/terraform
terraform destroy
```

This deletes every resource in the state. Key Vault soft-delete keeps secrets recoverable for 7 days; pass `-var="purge_kv=true"` to purge immediately (not implemented — patch `providers.tf` if needed).

## Why local state for now

Multi-developer or CI workflows need remote state on an Azure Storage backend. To bootstrap:

1. Manually create a Storage Account + container in a separate resource group (state must outlive any `terraform destroy` of the main stack).
2. Uncomment the `backend "azurerm" {...}` block in `main.tf`.
3. `terraform init -migrate-state`.

Skipped here because (a) the user is solo, (b) bootstrapping the backend itself adds another resource that has to live outside Terraform, and (c) local state is the lower-friction path for a single dev box.
