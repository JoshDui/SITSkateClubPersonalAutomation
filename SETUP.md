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

## 5. Create GitHub repo + configure secrets

1. Create a new GitHub repo (public is fine — no secrets in the code):
   ```bash
   gh repo create skatetelegrambot-aws --public --source=. --remote=origin --push
   ```
   Or via https://github.com/new.

2. In GitHub → repo → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**:
   - `AWS_ACCESS_KEY_ID` — value from step 4
   - `AWS_SECRET_ACCESS_KEY` — value from step 4
   - `TG_BOT_TOKEN` — from BotFather (same token as Phase 3)
   - `TG_WEBHOOK_SECRET` — generate: `python -c "import secrets; print(secrets.token_urlsafe(32))"`
   - `POWER_AUTOMATE_URL` — Phase 4 flow URL (or placeholder `https://example.com/pa-placeholder` for v1)

3. In GitHub → repo → **Settings** → **Environments** → **New environment** → `prod`
   - Add required reviewer (yourself) so `terraform apply` on main requires a manual click.

---

## 6. Populate SSM parameters (one-time, after first terraform apply)

The Terraform creates empty SSM parameter *slots* but doesn't store secret values (never put secrets in IaC). Fill them manually after the first `terraform apply`:

```bash
# Bot token
aws ssm put-parameter --profile skatebot \
  --name /skatebot/prod/bot_token \
  --type SecureString \
  --value "<BotFather token>" \
  --overwrite

# Webhook secret (must match the one in GitHub Actions secret)
aws ssm put-parameter --profile skatebot \
  --name /skatebot/prod/webhook_secret \
  --type SecureString \
  --value "$(python -c 'import secrets; print(secrets.token_urlsafe(32))')" \
  --overwrite

# Power Automate URL (leave as placeholder if Phase 4 flow not ready yet)
aws ssm put-parameter --profile skatebot \
  --name /skatebot/prod/power_automate_url \
  --type String \
  --value "https://prod-xx.westus.logic.azure.com/workflows/..." \
  --overwrite

# Admin Telegram user IDs (comma-separated)
aws ssm put-parameter --profile skatebot \
  --name /skatebot/prod/admin_ids \
  --type String \
  --value "123456789,987654321" \
  --overwrite

# Group chat ID
aws ssm put-parameter --profile skatebot \
  --name /skatebot/prod/group_chat_id \
  --type String \
  --value "-100123456789" \
  --overwrite
```

---

## 7. Register webhook with Telegram

After the first successful `terraform apply`, Terraform output will include the webhook Lambda's Function URL. Register it with Telegram:

```bash
python scripts/setwebhook.py
```

This script reads the URL from Terraform outputs and calls Telegram's `setWebhook` API with the matching secret from SSM.

---

## 8. (Optional) Migrate existing Phase 3 data

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
