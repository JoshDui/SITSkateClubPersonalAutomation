# Deployment Progress Log

Session handoff document. Snapshot of where deployment stands so future-Joshua can resume without re-reading the whole chat history.

**Last updated:** 2026-04-21
**Branch:** `main` (tracking `origin/main`)
**Last commit:** `44dd0e8` — "Add build.ps1 -- Windows equivalent of make package"
**Region:** `ap-southeast-1` (Singapore)

---

## Overall status: 4 of 5 deployment milestones complete

| # | Milestone | Status |
|---|---|---|
| M1 | AWS account + billing alarms + local tooling | ✅ Done |
| M2 | IAM deploy user + AWS CLI configured | ✅ Done |
| M3 | GitHub repo created + code pushed | ✅ Done |
| M4 | First `terraform apply` — 39 AWS resources live | ✅ Done |
| M5 | Populate SSM + register Telegram webhook + smoke test | ⏳ Pending |

The bot is **deployed but not yet functional** — Lambda/DynamoDB/EventBridge all exist on AWS, but SSM parameters are empty placeholders so the webhook Lambda can't read the bot token yet. Telegram webhook is also not registered. Neither step changes any infrastructure; both are pure config.

---

## What's currently deployed on AWS

Verified by `terraform apply` output (39 resources):

### Lambda functions (3)
- `skatebot-prod-webhook` — Telegram webhook handler (has a Function URL, exposed at `*.lambda-url.ap-southeast-1.on.aws`)
- `skatebot-prod-scheduler` — weekly cron target
- `skatebot-prod-exporter` — 24h-post-session export trigger target

### DynamoDB tables (3)
- `skatebot-prod-members` — PK: `username`
- `skatebot-prod-sessions` — PK: `id` (ULID); GSI `by_date`
- `skatebot-prod-responses` — PK: `session_id`, SK: `{category}#{telegram_id}`

### EventBridge
- Rule `skatebot-prod-weekly-poll` — `cron(0 10 ? * SUN *)` (10:00 UTC = 18:00 SGT Sunday). Target: scheduler Lambda.

### CloudWatch
- 3 log groups (`/aws/lambda/skatebot-prod-{webhook,scheduler,exporter}`), 14-day retention
- 3 error alarms (one per Lambda), SNS topic `skatebot-prod-alarms`

### SSM Parameter Store (all contain placeholder values as of now)
- `/skatebot/prod/bot_token` (SecureString)
- `/skatebot/prod/webhook_secret` (SecureString)
- `/skatebot/prod/power_automate_url` (SecureString)
- `/skatebot/prod/admin_ids` (String)
- `/skatebot/prod/group_chat_id` (String)
- `/skatebot/prod/rental_skates_handle` (String, already populated with `NotDrivingUnderInfluence`)

### IAM
- 4 roles: one per Lambda (webhook/scheduler/exporter) + 1 EventBridge Scheduler target role
- Policies scoped to specific table ARNs + SSM parameter path

### Billing
- Manual Budget: `skatebot-alarm-1usd` ($0.01 threshold — deliberately ultra-sensitive)

### Outputs (from `terraform output`)
- `webhook_function_url` — the Lambda Function URL to register with Telegram (run `terraform output webhook_function_url` from `infra/terraform/` to retrieve)
- `dynamodb_tables`, `{webhook,scheduler,exporter}_lambda_name`, `ssm_parameter_names`

---

## Local machine state

### Tools installed
- AWS CLI v2.34.32 (winget)
- Terraform v1.9.8 (manual install at `C:\tools\terraform\`)
- Python 3.13 (pre-existing)
- Git (pre-existing)

### AWS CLI profile
- Profile name: `skatebot`
- Default region: `ap-southeast-1`
- Activated via: `$env:AWS_PROFILE = "skatebot"` (set per session, or permanent via `[Environment]::SetEnvironmentVariable("AWS_PROFILE", "skatebot", "User")`)

### IAM user
- `skatebot-deploy` in account — has `AdministratorAccess`
- Active access key (CSV downloaded, stored locally)
- Old access key **rotated + deleted** after an early-session leak

### Repo
- Public GitHub repo: `https://github.com/JoshDui/SITSkateClubPersonalAutomation`
- Branch `main` tracks `origin/main`
- 2 GitHub Actions secrets set: `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`
- `prod` environment created with "Required reviewers = self" protection rule

---

## What's left — M5 (15–30 minutes)

### Step 1 — Populate SSM with real values

In PowerShell (`$env:AWS_PROFILE = "skatebot"` first if new session):

```powershell
# Bot token (same one used in Phase 3; check old .env or BotFather)
aws ssm put-parameter --name /skatebot/prod/bot_token --type SecureString --value "<PASTE BOT TOKEN>" --overwrite

# Generate a webhook secret (random 32 bytes, URL-safe)
$secret = python -c "import secrets; print(secrets.token_urlsafe(32))"
aws ssm put-parameter --name /skatebot/prod/webhook_secret --type SecureString --value "$secret" --overwrite

# Admin Telegram user IDs (comma-separated, your own @userinfobot ID)
aws ssm put-parameter --name /skatebot/prod/admin_ids --type String --value "<YOUR TG USER ID>" --overwrite

# Group chat ID (negative number for groups, get via @userinfobot added to the group)
aws ssm put-parameter --name /skatebot/prod/group_chat_id --type String --value "-100<GROUP CHAT ID>" --overwrite

# Power Automate URL — leave as placeholder until Phase 4 of the original plan is done
aws ssm put-parameter --name /skatebot/prod/power_automate_url --type SecureString --value "https://example.com/placeholder-power-automate-url" --overwrite
```

### Step 2 — Register the webhook with Telegram

```powershell
cd C:\UniPain\skatetelegrambot-aws
pip install -r requirements-dev.txt  # if not already installed
python scripts/setwebhook.py
```

This script reads the Function URL from `terraform output` and the secret from SSM, then calls Telegram's `setWebhook` API.

### Step 3 — Smoke test in Telegram

1. In the club's Telegram group, admin sends `/sendpoll`
2. Poll message appears
3. Tap buttons → names update live under the correct categories
4. `/summary` shows the current state
5. Check CloudWatch logs: `aws logs tail /aws/lambda/skatebot-prod-webhook --follow` should show inbound webhooks

### Step 4 — (Optional) Migrate Phase 3 data

```powershell
python scripts/migrate_sqlite_to_dynamo.py --source C:/UniPain/skatetelegrambot/skatebot.db --env prod
```

Idempotent — safe to run multiple times. Preserves old integer session IDs as strings so any cross-table references stay valid.

---

## Resume instructions (new PowerShell session)

```powershell
# Set the AWS profile (skip if set permanently)
$env:AWS_PROFILE = "skatebot"

# Verify AWS is still reachable
aws sts get-caller-identity  # should show arn:aws:iam::.../user/skatebot-deploy

# Navigate to the project
cd C:\UniPain\skatetelegrambot-aws

# Verify terraform state is accessible
cd infra\terraform
terraform output                  # should show webhook_function_url etc.
cd ..\..

# Continue with M5 steps above
```

---

## Known quirks encountered this session (for future reference)

1. **AWS signup "Free plan" is a trap** — it's a new 2024 restricted tier that auto-closes your account after 6 months. The "Paid plan" is the correct choice for persistent workloads; it doesn't bill if you stay within Always Free.
2. **Terraform winget package is unlisted** — HashiCorp's winget entry has been broken since ~2023. Manual install from `releases.hashicorp.com` is the reliable path.
3. **PowerShell backtick line continuations are paste-hostile** — prefer single-line commands or a `.ps1` script when copy-pasting multi-line PowerShell.
4. **`aws sts get-caller-identity` shows the IAM USER, not the access key** — same output before and after key rotation. Use `aws iam list-access-keys` to verify key rotation actually happened.
5. **GitHub Free + private repo = no deployment protection rules** — had to flip the repo to public to get the Required Reviewers gate. Public is actually better for portfolio purposes anyway; no secrets exist in the code (everything is in SSM).
6. **Region accidentally started on ap-southeast-2 (Sydney)** — the console defaulted to it on signup. Was manually switched to ap-southeast-1 (Singapore) before any resources were created. Terraform pins the region explicitly so this couldn't have caused drift.
7. **AWS credentials briefly leaked in a screenshot** — immediately rotated the key, deactivated the old one, and deleted it. New key verified working before proceeding.

---

## Useful commands reference

```powershell
# AWS
aws sts get-caller-identity                           # verify creds
aws iam list-access-keys --user-name skatebot-deploy  # verify key rotation
aws ssm get-parameter --name /skatebot/prod/bot_token --with-decryption   # read a secret
aws logs tail /aws/lambda/skatebot-prod-webhook --follow                  # tail webhook logs
aws lambda invoke --function-name skatebot-prod-scheduler --payload '{}' /tmp/out.json  # manual cron trigger

# Terraform (from infra/terraform/)
terraform plan                 # show what would change
terraform apply                # apply changes (asks for yes confirmation)
terraform output               # show all outputs
terraform output -raw webhook_function_url  # just the URL
terraform destroy              # tear down EVERYTHING — only if you mean it

# Git
git status                     # see uncommitted changes
git push                       # push to origin/main
git log --oneline -n 5         # recent commits

# Build
.\build.ps1                    # build all 3 Lambda zips locally
```

---

## Related docs

- [`SETUP.md`](./SETUP.md) — original manual-steps checklist (pre-deploy guide)
- [`README.md`](./README.md) — architecture + design rationale
- [`../skatetelegrambot/PLAN.md`](../skatetelegrambot/PLAN.md) — original Phase 3 plan + Power Automate payload spec
- [`../skatetelegrambot/Progress.md`](../skatetelegrambot/Progress.md) — Phase 3 (monolithic version) progress log
