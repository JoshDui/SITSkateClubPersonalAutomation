# Azure Deployment

Parallel Azure implementation of the SIT Inline Skate Bot, deployed alongside the AWS version on `main`.

## Status

Work-in-progress on `feat/azure-port` branch. AWS deployment on `main` is unaffected — both clouds eventually run the same bot.

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

```powershell
# 1. Provision infrastructure
cd azure/infra/terraform
terraform init
terraform apply

# 2. Publish function code
cd ../../functionapp
func azure functionapp publish skatebot-prod-azure --python

# 3. Register webhook with Telegram
cd ../scripts
python setwebhook.py
```

## Teardown

```powershell
cd azure/infra/terraform
terraform destroy
```

## Why a parallel deployment?

The AWS deployment on `main` is complete and proven. Azure is a parallel "deployed to two clouds" portfolio piece — same skate bot, same end-user behavior, different cloud primitives.

## Honest framing

For 107 users, neither AWS nor Azure microservice deployment is technically required — a single VPS would do the job. This codebase is decomposed for **learning purposes**: explicit AWS→Azure primitive mapping is a common SG enterprise interview topic, and "deployed to two clouds" is a stronger portfolio story than "shipped on AWS."

## See also

- Top-level [`README.md`](../README.md) — multi-cloud overview
- [`../infra/terraform/`](../infra/terraform/) — AWS Terraform
- [`../services/`](../services/) — AWS Lambda handlers
