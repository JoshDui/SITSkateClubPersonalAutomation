# Azure Deployment

Parallel Azure implementation of the SIT Inline Skate Bot, deployed alongside the AWS version. Both clouds now live on `main` after the multi-cloud port merged via PR #1 (commit `1fa5ad6`, 2026-04-30).

## Status

Deployed and end-to-end validated. Azure is the **active deployment** (Telegram webhook is currently registered to the Azure Function URL); AWS code is parallel-deployed but dormant pending M5 + the AWS export pivot.

- ✅ Webhook → Cosmos write → Telegram poll round-trip verified
- ✅ Scheduler manual-trigger idempotency verified
- ✅ Exporter Queue Trigger fires on visibility expiry → uploads CSV to Blob Storage (`attendance/{date}_{ulid}.csv`) — pivoted from Power Automate POST in B1, see PROGRESS.md "Attendance Export Pivot"
- ✅ Local `azure/scripts/build_attendance_report.py` produces `Attendance.xlsx` from Cosmos via `az login`
- ✅ Terraform `azurerm` IaC code complete (`azure/infra/terraform/`) — `terraform validate` clean
- ⬜ Terraform import of existing manual deployment (`import.ps1` written, not yet run)
- ⬜ GitHub Actions deploy pipeline with OIDC federation (A8)

Detailed session handoff with all 16 errors hit during deployment: [`PROGRESS.md`](./PROGRESS.md).

**Currently deployed resources** (manual, japaneast region — see PROGRESS.md error #1 for region-policy backstory):
- Resource Group: `rg-skatebot-prod`
- Function App: `skatebot-prod-azure-32441` (Linux Consumption, Python 3.12)
- Cosmos DB Table API: free tier, 3 tables (`members`, `sessions`, `responses`)
- Key Vault: `skatebot-prod-kv-29024` with core bot secrets plus optional topic-routing secrets
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
| Power Automate webhook (still in AWS, blocked by Premium) | Blob Storage CSV + local `openpyxl` script (B1 — free, no Premium) |

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
│   ├── setwebhook.py                  # Register the Telegram webhook to the Azure Function URL
│   ├── migrate_aws_to_azure.py        # (stub) one-shot DynamoDB → Cosmos copy
│   └── build_attendance_report.py     # On-demand Excel report from live Cosmos data
├── PROGRESS.md          # Session-handoff log + 16 errors + B1 rollout log
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
