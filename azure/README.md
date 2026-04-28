# Azure Deployment

Parallel Azure implementation of the SIT Inline Skate Bot, deployed alongside the AWS version on `main`.

## Status

Deployed and end-to-end validated on `feat/azure-port` branch (2026-04-28). AWS deployment on `main` is unaffected.

- ✅ Webhook → Cosmos write → Telegram poll round-trip verified
- ✅ Scheduler manual-trigger idempotency verified
- ✅ Exporter Queue Trigger fires on visibility expiry, payload built, Power Automate POST path exercised
- ⬜ Terraform IaC (currently provisioned imperatively via `az` CLI)
- ⬜ GitHub Actions deploy pipeline with OIDC federation

Detailed session handoff with all 16 errors hit during deployment: [`PROGRESS.md`](./PROGRESS.md).

**Currently deployed resources** (manual, japaneast region — see PROGRESS.md error #1 for region-policy backstory):
- Resource Group: `rg-skatebot-prod`
- Function App: `skatebot-prod-azure-32441` (Linux Consumption, Python 3.12)
- Cosmos DB Table API: free tier, 3 tables (`members`, `sessions`, `responses`)
- Key Vault: `skatebot-prod-kv-29024` with 6 secrets
- Storage Queue: `export-queue` on the Function App's Storage Account
- Application Insights: linked to Function App, queries via Azure Portal Logs blade

## Architecture (vs AWS)

| AWS | Azure |
|---|---|
| Lambda Function URL | Functions HTTP Trigger (anonymous auth) |
| EventBridge Rule (cron) | Functions Timer Trigger (NCRONTAB) |
| EventBridge Scheduler one-off `at()` | Storage Queue with `visibility_timeout=86400` |
| 3 separate Lambdas | **1 Function App with 3 functions** (Azure-idiomatic) |
| DynamoDB | Cosmos DB Table API (free tier) |
| SSM Parameter Store + KMS | Azure Key Vault |
| CloudWatch Logs + Alarms | Application Insights + Azure Monitor |
| AWS Budgets | Cost Management Budget |
| IAM Roles | System-Assigned Managed Identity + RBAC |

## Layout

```
azure/
├── functionapp/         # 1 Function App, 3 functions inside
│   ├── webhook/         # HTTP Trigger (Telegram webhook ingress)
│   ├── scheduler/       # Timer Trigger (weekly cron)
│   ├── exporter/        # Queue Trigger (24h post-session export)
│   ├── shared/          # db, config, poll, telegram, importer
│   ├── host.json        # Functions runtime config
│   └── requirements.txt
├── infra/terraform/     # azurerm provider IaC
├── scripts/
│   ├── setwebhook.py
│   └── migrate_aws_to_azure.py
└── tests/
```

## Deploy

**Currently** (imperative provisioning, A7 IaC pending):

```powershell
# Function code only — resources already provisioned
cd azure/functionapp
func azure functionapp publish skatebot-prod-azure-32441 --python --build remote
```

**Planned (post-A7)**:

```powershell
# 1. Provision infrastructure
cd azure/infra/terraform
terraform init && terraform apply

# 2. Publish function code
cd ../../functionapp
func azure functionapp publish skatebot-prod-azure --python --build remote

# 3. Register webhook with Telegram
cd ../scripts
python setwebhook.py
```

### Why `--build remote`

Linux Consumption needs Linux-built wheels. A Windows dev box cannot produce them locally; remote build runs `pip install` on Azure's deployment server (~2–3 min). See PROGRESS.md error #7.

## Teardown

```powershell
# Currently (imperative)
az group delete --name rg-skatebot-prod --yes --no-wait

# Post-A7 (Terraform)
cd azure/infra/terraform && terraform destroy
```

## Why a parallel deployment?

The AWS deployment on `main` is complete and proven. Azure is a parallel "deployed to two clouds" portfolio piece — same skate bot, same end-user behavior, different cloud primitives.

## Honest framing

For 107 users, neither AWS nor Azure microservice deployment is technically required — a single VPS would do the job. This codebase is decomposed for **learning purposes**: explicit AWS→Azure primitive mapping is a common SG enterprise interview topic, and "deployed to two clouds" is a stronger portfolio story than "shipped on AWS."

## See also

- Top-level [`README.md`](../README.md) — multi-cloud overview
- [`../infra/terraform/`](../infra/terraform/) — AWS Terraform
- [`../services/`](../services/) — AWS Lambda handlers
