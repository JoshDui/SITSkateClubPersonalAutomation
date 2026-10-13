# Manual Setup (Things Joshua Must Do by Hand)

Everything in this repo can be deployed by `terraform apply` + `git push` — but a few one-time setup steps require human action in browser consoles. Do these in order.

> All browser-console steps correspond to **Milestone M1** in the plan. Do them *before* running any `terraform` command.

---

## 1. Create AWS account

- Go to https://aws.amazon.com → **Create an AWS Account**
- Use your school email
- **Pick "Paid plan"** during account plan selection (see note below — counterintuitive but correct)
- Complete identity verification + payment method (you'll be pre-authed $1, not charged)
- Wait for activation email (usually 15–60 min)

### Why Paid plan, not Free plan?

AWS restructured signup in 2024. The "Free plan" is a restricted experimental tier that **closes your account after 6 months**. Our bot is meant to run forever.

The "Paid plan" includes the exact same $200 credit AND the traditional **Always Free tier** (1M Lambda requests/mo, 25 GB DynamoDB, etc.) which shields this bot's workload from charges permanently. "Paid" is a misnomer — you pay $0.00 as long as you stay within Always Free, which this bot does by four orders of magnitude.

---

## 2. Set billing alarms FIRST — before anything else

This is the only step you cannot skip. Do this before creating a single resource.

1. AWS Console → **Billing and Cost Management** → **Budgets** → **Create budget**
2. Budget type: **Cost budget — Monthly**
3. Create three separate budgets with email alerts:
   - `skatebot-alarm-1usd` — threshold $1.00
   - `skatebot-alarm-5usd` — threshold $5.00
   - `skatebot-alarm-10usd` — threshold $10.00
4. Email recipient: your school email (or whichever you check)

If any of these fire, something is misconfigured. Investigate before paying.

> Terraform *can* manage these — see `infra/terraform/budget.tf`. But you should create them manually first so you're protected before the first `terraform apply` runs.

---

## 3. Install local tooling

On your laptop (Windows PowerShell, WSL, or Git Bash):

```bash
# AWS CLI v2
winget install -e --id Amazon.AWSCLI
# or from https://aws.amazon.com/cli/

# Terraform 1.7+
winget install -e --id HashiCorp.Terraform
# or from https://developer.hashicorp.com/terraform/downloads

# Python 3.12+
winget install -e --id Python.Python.3.12

# Docker Desktop (for local DynamoDB testing)
# https://docs.docker.com/desktop/install/windows-install/
```

Verify:
```bash
aws --version       # aws-cli/2.x
terraform --version # v1.7+
python --version    # 3.12.x
docker --version
```

---

## 4. Create IAM user for Terraform + CI

In AWS Console → **IAM** → **Users** → **Create user**

- Name: `skatebot-deploy`
- Attach policy: `AdministratorAccess` (scope down later; see `infra/terraform/iam.tf` note)
- Create access key → **Use case: Command Line Interface (CLI)**
- Save the Access Key ID + Secret Access Key to your password manager

Configure the AWS CLI on your laptop:
```bash
aws configure --profile skatebot
# AWS Access Key ID: <paste>
# AWS Secret Access Key: <paste>
# Default region: ap-southeast-1
# Default output format: json
```

Set the profile for your shell session:
```bash
export AWS_PROFILE=skatebot    # bash/zsh
$env:AWS_PROFILE = "skatebot"  # PowerShell
```

---

## 5. GitHub repo (CI only)

CI (`.github/workflows/ci.yml`) runs lint, tests, packaging and
`terraform validate` on every push and PR. It needs **no secrets** and never
deploys: Terraform state lives only on the deploying machine
(`infra/terraform/terraform.tfstate`, gitignored), and a fresh CI runner would
try to recreate every resource. Deploys run locally (below).

---

## 6. Deploy and populate SSM parameters

```powershell
$env:AWS_PROFILE = "skatebot"
python scripts\package_lambdas.py          # builds dist/*.zip for Lambda
cd infra\terraform
terraform plan                              # review: nothing should be replaced
terraform apply
cd ..\..
```

Terraform creates the secret parameter *slots* with placeholders (never put
secrets in IaC); `group_chat_id` and `group_topic_id` are not secret and are
set from `variables.tf`. Fill the secrets once, in your own terminal (the
token prompt is hidden and stays out of shell history):

```powershell
.\scripts\bootstrap-ssm.ps1                 # bot_token, webhook_secret, admin_ids
python scripts\find_admin_ids.py --write   # resolve the 5 admins' numeric IDs
```

Admin authorization uses numeric Telegram IDs, never handles.

---

## 7. Function URL access (one-time, outside Terraform)

Since October 2025 a public (`AuthType NONE`) Lambda Function URL needs two
resource-policy statements. AWS provider 5.x cannot express the second
condition, so both are added with the CLI, for **each** public function:
`skatebot-prod-webhook` (Telegram) and `skatebot-prod-webform` (QR-code
sign-up pages). Re-run only if a function is ever recreated:

```powershell
foreach ($fn in "skatebot-prod-webhook", "skatebot-prod-webform") {
  aws lambda add-permission --function-name $fn `
    --statement-id FunctionURLAllowPublicAccess --action lambda:InvokeFunctionUrl `
    --principal "*" --function-url-auth-type NONE
  aws lambda add-permission --function-name $fn `
    --statement-id FunctionURLInvokeAllowPublicAccess --action lambda:InvokeFunction `
    --principal "*" --invoked-via-function-url
}
```

Checks: a webhook POST without the secret header returns **401**, and a web
form GET of `/e/x` returns **404** (both are the handlers answering). A
**403** means the policy is still incomplete.
https://docs.aws.amazon.com/lambda/latest/dg/urls-auth.html

**Web form kill switch.** If the public sign-up pages are ever flooded, stop
them instantly (free; the Telegram bot keeps working), and undo it later:

```powershell
aws lambda put-function-concurrency --function-name skatebot-prod-webform --reserved-concurrent-executions 0
aws lambda delete-function-concurrency --function-name skatebot-prod-webform
```

---

## 8. Register webhook with Telegram

```powershell
python scripts\setwebhook.py
```

Reads the URL from Terraform outputs and calls Telegram's `setWebhook` with
the matching secret from SSM. `getWebhookInfo` (printed at the end) should
show the Lambda URL with no `last_error_message`.

---

## 9. (Optional) Migrate existing Phase 3 data

If you want to preserve the members already imported in Phase 3's `skatebot.db`:

```bash
# Point the migration script at the Phase 3 SQLite file
python scripts/migrate_sqlite_to_dynamo.py --source C:/UniPain/skatetelegrambot/skatebot.db
```

Idempotent — safe to re-run.

---

## Verification after all manual steps

Run the plan:
```bash
cd infra/terraform
terraform init
terraform plan
```

If it shows only resource creations (no errors, no "unknown value" warnings about missing secrets), you're good. Proceed with `terraform apply`.

First-time deploy then smoke test: send `/sendpoll` to the bot in Telegram and confirm the poll appears.
