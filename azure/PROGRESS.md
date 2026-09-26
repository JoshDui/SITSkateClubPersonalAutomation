# Azure Deployment Progress

**Branch:** `main` (post-merge — `feat/azure-port` preserved on origin for reference)
**Last commit:** `1fa5ad6` — Merge pull request #1 from JoshDui/feat/azure-port
**Pivot commits:** `f64207e` (B1) + `43a1dbb` (B1 follow-up: gitignore, missing dep, smoke-test note)
**Last updated:** 2026-05-01
**Status:** Multi-cloud port **shipped to `main`** via PR #1 (https://github.com/JoshDui/SITSkateClubPersonalAutomation/pull/1, merged 2026-04-30T22:50Z). Azure deployment is the live deployment and end-to-end verified: Telegram → Webhook → Cosmos → Queue → Exporter → Blob CSV → local `Attendance.xlsx`. AWS deployment remains parallel-deployed but dormant (M5 unpopulated; AWS exporter still POSTs to dead Power Automate URL and silently no-ops on the placeholder).

**Branch strategy (Path A):**
1. ✅ Roll out the export pivot on `feat/azure-port` (manual `az` block — done 2026-04-30, see "Session Log: B1 Pivot Rollout" below)
2. ✅ Open `feat/azure-port` → `main` PR; merge once Azure is fully green (PR #1, merged 2026-04-30)
3. ⏭️ From the new `main`, branch `feat/aws-export-pivot` to mirror the Blob/CSV pattern on S3 for the AWS Lambda exporter — AWS verification cleared 2026-04-29, so M5 + the AWS pivot are unblocked when ready.

**Branch strategy (Path A, locked in 2026-04-29):**
1. Roll out the export pivot on `feat/azure-port` (manual `az` block in "Rollout steps" → smoke test → commit)
2. Open `feat/azure-port` → `main` as a single PR; merge once Azure is fully green
3. **From the new `main`**, branch `feat/aws-export-pivot` to mirror the Blob/CSV pattern on S3 for the AWS Lambda exporter — AWS verification has cleared (2026-04-29), so M5 + the AWS pivot are unblocked and can be done after the Azure merge

This keeps the AWS pivot off `feat/azure-port` (no branch contamination), and the AWS branch starts from a `main` that already carries the multi-cloud README + the "AWS pivot deferred" footnote, giving its eventual PR a clean narrative ("as foreshadowed, mirror Azure on S3").

This document is a session handoff so either of us can resume cold. Read top-to-bottom: context → resource inventory → milestone status → errors-and-fixes → current blocker → resume steps.

---

## Context

Parallel multi-cloud port of the SIT Inline Skate Club Telegram bot. AWS deployment is complete and verified (39 resources, 3 Lambdas). Azure deployment is being built side-by-side as a portfolio piece. AWS code on `main` stays untouched until `feat/azure-port` is merged as a single PR.

**Architectural choices** (set during planning, do not change without re-discussion):
- 1 Function App, 3 functions (`webhook` HTTP, `scheduler` Timer, `exporter` Queue) — *not* 3 Function Apps
- Cosmos DB Table API on free tier (1000 RU/s + 25 GB)
- Storage Queue with `visibility_timeout=86400` replaces EventBridge Scheduler one-off
- Azure Key Vault + System-Assigned Managed Identity (no SP secret in env vars)
- Region: `japaneast` (forced — see error #1)
- Terraform `azurerm` to come in A7; resources currently provisioned imperatively via `az` CLI

---

## Provisioned Resources (manual — to be Terraform'd in A7)

| Resource | Name / Identifier | Notes |
|---|---|---|
| Subscription | Azure for Students under SIT Entra tenant | Region policy whitelist enforced |
| Resource Group | `rg-skatebot-prod` | All resources here |
| Region | `japaneast` | After eastasia hit ServiceUnavailable |
| Cosmos DB Account | (Table API, free tier enabled) | Endpoint stored in Function App app settings |
| Cosmos Tables | `members`, `sessions`, `responses` | PartitionKey strategy in `azure/functionapp/shared/db.py` docstring |
| Storage Account | (auto-named, used by Function App + Storage Queue) | Hosts `export-queue` |
| Storage Queue | `export-queue` | Used by exporter; max `visibility_timeout` 7 days |
| App Service Plan | (Y1 Consumption Linux) | Auto-created by `az functionapp create --consumption-plan-location` |
| Function App | `skatebot-prod-azure-32441` | Linux, Python 3.12, 3 functions deployed |
| Application Insights | (auto-created with Function App) | Linked Log Analytics workspace |
| Key Vault | `skatebot-prod-kv-29024` | RBAC authorization mode (not access policies) |
| Managed Identity | System-Assigned on Function App | Has Key Vault Secrets User + Cosmos Data Contributor + Storage Queue Data Contributor |

### Key Vault Secrets (all lowercase-hyphenated — not `SCREAMING-KEBAB`)

```
admin-ids
bot-token
group-chat-id
power-automate-url
rental-skates-handle
webhook-secret
```

`shared/config.py` reads these; the deployed Function App resolves them via Managed Identity at cold start (`@functools.cache`).

### Function App URLs

- Webhook (HTTP Trigger): `https://skatebot-prod-azure-32441.azurewebsites.net/api/webhook`
- Telegram webhook is registered to this URL; only one webhook per bot — switch back to AWS via `setwebhook.py` to test AWS again.

---

## Milestone Status

| ID | Milestone | Status |
|---|---|---|
| A1 | Azure account + tooling (CLI, Functions Core Tools v4, Azurite, Node 22) | ✅ Done |
| A2 | Branch + scaffold + portable module copy (`poll.py`, `telegram.py`, `importer.py` verbatim) | ✅ Done — commit `e1c3c44` |
| A3 | Cosmos DB + config layer (`shared/db.py` + `shared/config.py` rewritten) | ✅ Done — commit `4621db7` |
| A4 | Function App + webhook function | ✅ Done — deployed, button taps + Cosmos writes verified |
| A5 | Scheduler function + Storage Queue | ✅ Done — manual timer trigger returned 202; queue message enqueued |
| A6 | Exporter function (original Power Automate POST) | ✅ Code shipped, but the sink died on Premium licensing — see B1 |
| A7 | Terraform azurerm IaC | 🟢 Code complete (commit `8124bf7`), `terraform validate` clean, `import.ps1` provided; `terraform import` + `apply` pending user execution (A8 dependency) |
| A8 | Migration script + CI/CD | ⬜ Pending — `migrate_aws_to_azure.py` is a stub; deploy-azure.yml not written |
| A9 | README + multi-cloud framing | ✅ Done — top-level README + azure/README.md (commit `9231d52`) |
| A10 | End-to-end smoke test | 🟡 Mostly done — webhook + scheduler verified; final exporter→Excel walk-through pending the B1 rollout |
| B1 | **Export pivot** (Power Automate → Blob CSV + local openpyxl) | ✅ Done — code commit `f64207e`, manual `az` rollout applied 2026-04-30, smoke test green: queued message → CSV in `attendance` container → local script generated `Attendance.xlsx` cleanly |

---

## Errors Encountered + Fixes

These are real footguns that cost time. Reference here so neither of us re-discovers them.

### 1. Region policy blocks most regions (subscription-level)
**Symptom:** `RequestDisallowedByAzure` on Cosmos DB / Function App create in `southeastasia`, `eastus`, `eastus2`. Reason: Azure for Students under SIT's institutional Entra tenant has a region whitelist policy ("Allowed Locations" ASC Default).
**Allowed regions discovered:** `eastasia`, `japaneast`, `indonesiacentral`, `koreacentral`, `japanwest`.
**Tried:** `eastasia` → got `ServiceUnavailable` for Cosmos (capacity).
**Used:** `japaneast` — works for everything.
**Memory file:** `reference_azure_for_students_region_restrictions.md`.

### 2. Resource provider not registered (subscription-level, one-time per subscription)
**Symptom:** `MissingSubscriptionRegistration: The subscription is not registered to use namespace 'Microsoft.DocumentDB'`.
**Cause:** Fresh Azure subscriptions opt-in to each resource provider on first use.
**Fix:** Pre-register all needed providers up-front:
```powershell
az provider register --namespace Microsoft.DocumentDB
az provider register --namespace Microsoft.KeyVault
az provider register --namespace Microsoft.Storage
az provider register --namespace Microsoft.Web
az provider register --namespace Microsoft.Insights
az provider register --namespace Microsoft.OperationalInsights
az provider register --namespace Microsoft.ManagedIdentity
```
Each takes 1–2 minutes (async). Check with `az provider show --namespace X --query registrationState`.
**Memory file:** `reference_azure_resource_provider_registration.md`.

### 3. Cosmos DB stuck in failed-provisioning state after first error
**Symptom:** `BadRequest: failed provisioning state` on retry after region rejection.
**Fix:** `az cosmosdb delete --name <NAME> --resource-group rg-skatebot-prod --yes` before retrying create.

### 4. Azure CLI Python crash (0xC0000005) when installing `application-insights` extension
**Symptom:** Windows access violation in Azure CLI's bundled Python interpreter while running `az extension add --name application-insights`.
**Workaround:** Skip explicit App Insights creation. `az functionapp create` auto-creates a linked App Insights and Log Analytics workspace.

### 5. `Y1` SKU rejected by `az functionapp plan create`
**Symptom:** `Sku Y1 is not supported`.
**Cause:** `Y1` is the Consumption plan SKU and isn't created via `functionapp plan create`. Use `--consumption-plan-location` directly on `az functionapp create`.
**Correct flow:**
```
az functionapp create \
  --resource-group rg-skatebot-prod \
  --consumption-plan-location japaneast \
  --runtime python --runtime-version 3.12 \
  --functions-version 4 \
  --os-type Linux \
  --name <NAME> \
  --storage-account <STORAGE>
```

### 6. "Python not supported on Windows OS"
**Symptom:** `az functionapp create` rejects without `--os-type Linux`.
**Cause:** Python Functions only run on Linux.
**Fix:** Always pass `--os-type Linux` for Python Function Apps.

### 7. `func` couldn't determine project language during publish
**Symptom:** `Worker runtime cannot be 'None'...` during `func azure functionapp publish`.
**Fix:** Create `azure/functionapp/local.settings.json` with:
```json
{ "IsEncrypted": false, "Values": { "AzureWebJobsStorage": "", "FUNCTIONS_WORKER_RUNTIME": "python" } }
```
And always publish with `--python --build remote`.
**Why `--build remote`:** Linux Consumption needs Linux-built wheels. Windows dev box can't produce them locally.

### 8. PowerShell heredoc indentation
**Symptom:** Pasting a heredoc with the closing `'@` indented hangs the shell waiting for input.
**Fix:** Closing `'@` MUST be at column 0 (no leading whitespace) on its own line. Or write the file via the editor instead.

### 9. Linux Consumption: no `az webapp log tail` / `func azure functionapp logstream`
**Symptom:** Both commands return 404 or "not supported".
**Cause:** Linux Consumption doesn't support filesystem log streaming.
**Fix:** Use Application Insights via the **Azure Portal** (CLI extension is unavailable on this dev box — see error #13 below):
1. Portal → Resource Group `rg-skatebot-prod` → Application Insights resource
2. Left nav → **Logs**
3. Run KQL:
   ```kql
   exceptions
   | where timestamp > ago(30m)
   | order by timestamp desc
   | project timestamp, outerMessage, innermostMessage, details
   ```
4. Click the row to expand `details[0].rawStack` for the full Python traceback.

For real-time observation use **Live Metrics** (Application Insights → left nav → Live Metrics) — shows incoming requests + exceptions as they happen, no query needed.

### 10. PowerShell variable scope across terminals
**Symptom:** Variables defined in one terminal session are gone when a new one opens.
**Fix:** Re-declare `$VAULT`, `$URL`, `$APP_NAME`, `$SECRET` at the top of each session.

### 11. Cosmos DB Table API rejects `id` as reserved property name (KEY BUG)
**Symptom:**
```
HttpResponseError: 400 Bad Request
{"odata.error":{"code":"PropertyNameInvalid","message":{"value":"The property name 'id' is currently not supported."}}}
```
**Cause:** Unlike Azure Table Storage, Cosmos DB's Table API maps to its underlying SQL/document engine where `id` is a reserved system property (the document key).
**Fix:** Don't store `id` as an entity property. Derive `id = RowKey` at read time in `_entity_to_dict` for the SESSION partition. See commit `dcf73fc` in `azure/functionapp/shared/db.py`.
**Why this fix and not a caller-side change:** Plan was "mirror AWS public function names so handler ports stay mechanical" — leaking Cosmos's property-naming rules into business logic would diverge from the AWS port.

### 12. Key Vault secret naming is lowercase-hyphenated, not SCREAMING-KEBAB
**Symptom:** `SecretNotFound` for `TELEGRAM-WEBHOOK-SECRET`.
**Cause:** During provisioning, secrets were created with lowercase-hyphenated names (`webhook-secret`, `bot-token`, etc.) to match the keys read by `shared/config.py`. Key Vault names are case-sensitive.
**Fix:** Always list first: `az keyvault secret list --vault-name <VAULT> --query "[].name" -o tsv`.

### 13. `az monitor app-insights` extension cannot be installed on this dev box
**Symptom:**
```
ERROR: An error occurred. Pip failed with status code 3221225477.
The command requires the extension application-insights. Do you want to install it now? ...
```
Status `3221225477` is `0xC0000005` — Windows access violation. This is the same crash as error #4: Azure CLI's bundled Python interpreter dies during `pip install` on this machine.
**Workaround:** Use the Azure Portal Logs blade (see error #9 fix). The portal-based KQL editor and Live Metrics work identically and require no extension install.
**Possible root cause** (not investigated): Antivirus interference, corrupted Azure CLI install, or a Python wheel incompatible with this exact Windows build (10.0.26200). Reinstalling Azure CLI may help if a CLI-only workflow is needed later.

### 14. App Insights ingestion lag when debugging
**Symptom:** KQL query returns "No results found" immediately after a webhook failure.
**Cause:** Linux Consumption Functions batch-flush telemetry every ~30s + Microsoft's ingestion pipeline adds another 30–60s delay. A query within 60s of the request will legitimately show nothing.
**Fix:** Wait 60–90s after firing the test before running the KQL query. Or use Live Metrics (Application Insights → Live Metrics) which has ~1s latency but doesn't persist data — good for confirming a request landed, not for inspecting tracebacks.

### 15. Cosmos `id` fix did not unblock the smoke test — second bug was synthetic-test IDs
**Symptom:** After commit `dcf73fc` (the `id` reserved-property fix), the webhook still returned `200 error-logged` on `/sendpoll`. App Insights traceback pointed at:
```
File "/home/site/wwwroot/webhook/__init__.py", line 104, in _handle_message
    telegram.send_message(chat["id"], "⛔ Admin only.")
httpx.HTTPStatusError: 400 Bad Request from api.telegram.org/...sendMessage
```
**Cause:** The test payload used a placeholder `from.id` / `chat.id` (`7975723065`) that:
1. Was NOT in the real `admin-ids` Key Vault secret → the handler entered the "Admin only" reject branch
2. Was a fake user that has never DM'd the bot → Telegram refused to deliver the rejection message to that chat (bots cannot initiate DMs)
**Fix:** Always use real Telegram IDs for synthetic tests. Fetch them from Key Vault:
```powershell
$ADMIN_ID = az keyvault secret show --vault-name $VAULT --name admin-ids --query value -o tsv
$GROUP_ID = az keyvault secret show --vault-name $VAULT --name group-chat-id --query value -o tsv
```
Then in the smoke test payload:
- `from.id` = first entry of `$ADMIN_ID` (it's a comma-separated list)
- `chat.id` = `$GROUP_ID` (so replies land in the group where the bot is a member)
**Diagnostic that found it:** the `union exceptions, traces` KQL — looking at the *last successful trace* before the exception (the Key Vault `admin-ids` fetch) plus the exception line number (104) pinpointed the admin-rejection path, not a Cosmos or business-logic bug.

### 16. Cosmos Table API forbids `#` in RowKey/PartitionKey
**Symptom:** Real Telegram button tap → callback handler crashes with 400 Bad Request inserting into the `responses` table. Trace shows GET succeeded with `RowKey='non_sit%233844816317'` (URL-encoded `#`) returning 404 (expected), then POST insert returned 400.
**Cause:** Cosmos Table API (and Azure Table Storage) forbid these characters in keys: `/`, `\`, `#`, `?`, control chars 0x00–0x1F and 0x7F–0x9F. The AWS-style composite RowKey `f"{category}#{telegram_id}"` works on DynamoDB but not Cosmos.
**Fix:** Change separator from `#` to `:` in `azure/functionapp/shared/db.py::_response_rk` and update the OData prefix-range query in `get_responses_by_category` (`':'` → `';'` instead of `'#'` → `'$'`). The session entity's RowKey (a ULID) is unaffected — only the responses table uses composite keys.
**Why latent until now:** session entities use a ULID as RowKey (no `#`). Only the responses table builds composite RowKeys with the separator, so the bug surfaced on the first real button-tap, not the `/sendpoll` test which only writes to sessions.

---

## Current State (post-merge, 2026-05-01)

No active blockers. All Azure milestones (A1–A10) plus the B1 export pivot are shipped on `main`. The bot is **live on Azure** and processing real Telegram traffic. Verified end-to-end:

- Webhook receives Telegram updates, validates `X-Telegram-Bot-Api-Secret-Token`, writes to Cosmos
- Button taps via inline keyboard create/delete `responses` rows correctly (Cosmos `#`-in-RowKey bug fixed via `:` separator, see error #16)
- Scheduler timer trigger fires per NCRONTAB (Sunday 18:00 SGT); manual trigger creates `sessions` row + enqueues a 24h-delayed export message
- Exporter Queue Trigger fires when message visibility expires; reads session + responses from Cosmos, builds CSV, uploads to Blob (replaces the dead Power Automate POST)
- Local `azure/scripts/build_attendance_report.py` queries Cosmos via `az login` token and produces a valid `Attendance.xlsx` openable in Excel without an import wizard

Pending follow-ups (not blocking):

- **A8 — CI/CD**: `azure/scripts/migrate_aws_to_azure.py` is a stub; `.github/workflows/deploy-azure.yml` not yet written. Currently deploying via `func azure functionapp publish`.
- **Terraform import**: existing Azure resources were provisioned manually pre-Terraform; the IaC code is in the repo (commit `8124bf7`) but not yet bound to the running deployment via `terraform import`. `azure/infra/terraform/import.ps1` does the import in one shot.
- **AWS export pivot** (separate branch): Mirror B1 on S3 for the AWS exporter Lambda. Will land on `feat/aws-export-pivot` off the new `main` once started.
- **AWS M5**: Populate SSM (via `scripts/bootstrap-ssm.ps1`), switch the Telegram webhook to the AWS Function URL via `setwebhook.py`, run a parallel smoke test. Note: only one Telegram webhook can be active per bot, so testing AWS means temporarily disabling Azure ingress.

---

## How to Resume Cold

1. **Verify current state**
   ```powershell
   git -C C:\UniPain\skatetelegrambot-aws status
   git -C C:\UniPain\skatetelegrambot-aws log --oneline feat/azure-port -10
   az account show --query "{name:name, id:id}" -o table
   az resource list --resource-group rg-skatebot-prod --query "[].{name:name,type:type}" -o table
   ```

2. **Set session variables** (re-run on every new terminal — see error #10)
   ```powershell
   $RG = "rg-skatebot-prod"
   $APP_NAME = "skatebot-prod-azure-32441"
   $VAULT = "skatebot-prod-kv-29024"
   $URL = "https://$APP_NAME.azurewebsites.net/api/webhook"
   $SECRET = az keyvault secret show --vault-name $VAULT --name webhook-secret --query value -o tsv
   ```
   (Don't try `az monitor app-insights component show` — extension install fails on this box, see error #13. Use the portal for App Insights queries.)

3. **Inspect the latest exception** (via Azure Portal — CLI extension unavailable, see error #13)
   - Portal → `rg-skatebot-prod` → the Application Insights resource → **Logs**
   - Run:
     ```kql
     exceptions
     | where timestamp > ago(30m)
     | order by timestamp desc
     | take 5
     | project timestamp, outerMessage, innermostMessage, details
     ```
   - Expand the row → `details[0].rawStack` for the full Python traceback.

4. **Fix → commit → redeploy** (from `azure/functionapp/`)
   ```powershell
   cd C:\UniPain\skatetelegrambot-aws\azure\functionapp
   func azure functionapp publish $APP_NAME --python --build remote
   ```
   Remote build takes ~2–3 min.

5. **Re-run smoke test** — paste as a single unit. Always fetch real IDs from Key Vault (see error #15 — synthetic IDs cause Telegram 400 + admin-reject):
   ```powershell
   $ADMIN_ID = az keyvault secret show --vault-name $VAULT --name admin-ids --query value -o tsv
   $GROUP_ID = az keyvault secret show --vault-name $VAULT --name group-chat-id --query value -o tsv

   $body = @{
     update_id = 999100
     message = @{
       message_id = 1
       from = @{ id = [int64]($ADMIN_ID -split ',')[0]; is_bot = $false; first_name = "Joshua"; username = "joshuadui" }
       chat = @{ id = [int64]$GROUP_ID; type = "supergroup" }
       date = [int][double]::Parse((Get-Date -UFormat %s))
       text = "/sendpoll"
     }
   } | ConvertTo-Json -Depth 6 -Compress

   $resp = Invoke-WebRequest -Uri $URL -Method POST `
     -Headers @{ "X-Telegram-Bot-Api-Secret-Token" = $SECRET } `
     -ContentType "application/json" `
     -Body $body -UseBasicParsing
   "Status: $($resp.StatusCode), Body: $($resp.Content)"
   ```
   - Expect `200 ok`.
   - Then wait 60–90s (error #14) and check App Insights via the portal.
   - On full success: a row appears in Cosmos `sessions` table (Data Explorer) AND a poll message lands in the Telegram group, plus a "✅ Poll sent for session on ..." reply.

6. **Already verified** — webhook, scheduler, and exporter were all smoke-tested before the merge. To re-verify after a redeploy or infra change, follow steps 5 above (webhook), and the "Smoke test after rollout" section under "Attendance Export Pivot" for the Blob CSV + local Excel side.

7. **To regenerate the local Excel report on demand:**
   ```powershell
   pip install openpyxl azure-data-tables azure-identity azure-keyvault-secrets   # one-time
   python C:\UniPain\skatetelegrambot-aws\azure\scripts\build_attendance_report.py --since 2026-04-01
   start .\Attendance.xlsx
   ```

---

## Cost Posture (intentional)

- Cosmos DB free tier: 1000 RU/s + 25 GB free *forever* on this account
- Function App Consumption: 1M executions/month + 400k GB-s free *every month*
- App Insights: 5 GB/month free
- Storage: ~$0.01/month at our scale
- Key Vault: $0.03 per 10k operations (~$0.10/month)

**Expected monthly cost: $0–1.** Cost Management Budget should be set to alert at $1 / $5. (TODO: verify budget is wired up in `azure/infra/terraform/budget.tf` once A7 lands.)

---

## File Inventory (committed code)

| Path | Status |
|---|---|
| `azure/functionapp/webhook/__init__.py` | ✅ Ported, deployed, smoke-test pending unblock |
| `azure/functionapp/scheduler/__init__.py` | ✅ Ported, deployed, untested |
| `azure/functionapp/exporter/__init__.py` | ✅ Ported, deployed, untested |
| `azure/functionapp/shared/config.py` | ✅ Rewritten for Key Vault + DefaultAzureCredential |
| `azure/functionapp/shared/db.py` | ✅ Rewritten for Cosmos Table API; `id` bug fixed in `dcf73fc` |
| `azure/functionapp/shared/{poll,telegram,importer}.py` | ✅ Verbatim copies from AWS |
| `azure/functionapp/{host.json, requirements.txt, local.settings.json}` | ✅ Configured |
| `azure/functionapp/{webhook,scheduler,exporter}/function.json` | ✅ Trigger bindings configured |
| `azure/infra/terraform/*.tf` | ✅ Full stack written; `import.ps1` adopts existing manual deployment |
| `azure/scripts/setwebhook.py` | ⬜ Stub |
| `azure/scripts/migrate_aws_to_azure.py` | ⬜ Stub |
| `azure/scripts/build_attendance_report.py` | ✅ Local Excel report (export pivot) |
| `azure/tests/*.py` | ⬜ Stubs |
| `azure/README.md` | ⬜ Stub |

---

## Attendance Export Pivot (2026-04-28)

The original architecture had the exporter Function POST attendance JSON to a Power Automate flow that wrote rows into a SharePoint Excel file. That path is dead in this tenant:

1. **Power Automate Premium gate.** Power Automate's "When an HTTP request is received" trigger is a Premium connector. The flow saves but the runtime refuses to invoke it for accounts without Premium licensing — flow checker says: *"This flow's owner needs a Power Automate Premium license."* SIT student accounts do not include Premium.

2. **Microsoft Graph alternative blocked by tenant policy.** The natural replacement (register an AAD app with `Sites.ReadWrite.All` and write to Excel via Graph) requires `az ad app create`, which returns:

   ```
   Insufficient privileges to complete the operation.
   ```

   Joshua's account has no directory permissions in the SIT tenant, so neither AAD app registration nor admin consent for Graph permissions is achievable.

3. **AWS does not help.** The Power Automate dependency is licensed at the Microsoft-account level, not the cloud provider. The AWS exporter Lambda hits the same wall and the Graph workaround needs the same admin consent.

### New design — dual sink

| Sink | Producer | Consumer |
|---|---|---|
| Per-session CSV in Blob Storage container `attendance` | `azure/functionapp/exporter/__init__.py` (Queue Trigger) | Cloud-side audit trail; downloadable via `az storage blob` or Azure Portal Storage Browser |
| Local `Attendance.xlsx` workbook | `azure/scripts/build_attendance_report.py` (manual run) | The user, on demand, via `az login` + Cosmos query |

The cloud pipeline still runs end-to-end (Telegram → Webhook → Cosmos → Queue → Exporter → Blob), preserving the queue-trigger architectural story. The local script reads Cosmos directly (Cosmos is the source of truth) and produces the human-facing Excel; the Blob CSVs are the cloud-side audit trail / fallback.

### How to regenerate the Excel report

One-time setup (in your local venv or global Python — same packages the Function App already lists, just installed locally for the script's process):

```powershell
pip install openpyxl azure-data-tables azure-identity azure-keyvault-secrets
```

`azure-keyvault-secrets` is required even though the script never reads from Key Vault — `shared/config.py` does a top-level `from azure.keyvault.secrets import SecretClient` that fires at import time. Cheaper to install the dep than to refactor config.py into Key-Vault-optional layers.

Cold start, after `az login`:

```powershell
python C:\UniPain\skatetelegrambot-aws\azure\scripts\build_attendance_report.py
# Default: Attendance.xlsx in cwd, sessions from the last 90 days, skipped sessions filtered out

python ...\build_attendance_report.py --since 2026-01-01 --out Q1.xlsx
python ...\build_attendance_report.py --include-skipped
```

The script auto-discovers `COSMOS_ENDPOINT`, `TABLE_*`, and `KEY_VAULT_URL` from the deployed Function App's app settings via Azure CLI. Override with `--function-app` / `--resource-group` or pre-set env vars if pointing at a different deployment.

### Cosmos data-plane access for the running user

The local script needs the Cosmos DB Built-in Data Contributor role on Joshua's user identity (the FA's MI already has it; Joshua's user does not by default). `azure/infra/terraform/rbac.tf` now includes `azurerm_cosmosdb_sql_role_assignment.tf_cosmos_data_contrib` to grant this on `terraform apply`.

If you need to grant it ad-hoc before re-applying Terraform:

```powershell
$COSMOS = "<cosmos-account-name>"
$RG = "rg-skatebot-prod"
$PRINCIPAL = (az ad signed-in-user show --query id -o tsv)
az cosmosdb sql role assignment create `
  --account-name $COSMOS --resource-group $RG `
  --scope "/" `
  --role-definition-id 00000000-0000-0000-0000-000000000002 `
  --principal-id $PRINCIPAL
```

### Rollout steps (after merging this change)

The deployed Function App was provisioned manually pre-Terraform, so it does not yet have `BLOB_ACCOUNT_URL` / `ATTENDANCE_CONTAINER` app settings, the `attendance` container, or the new RBAC role assignments. Two ways to apply:

**Option A — Terraform (preferred, idempotent):**

```powershell
cd C:\UniPain\skatetelegrambot-aws\azure\infra\terraform
# If you haven't imported existing resources yet, run import.ps1 first.
terraform plan
terraform apply
```

The plan should show: `+ azurerm_storage_container.attendance`, `+ azurerm_role_assignment.fa_blob_data_contrib`, `+ azurerm_cosmosdb_sql_role_assignment.tf_cosmos_data_contrib`, and `~ azurerm_linux_function_app.main` (app settings update). Apply.

**Option B — Manual `az` (immediate, while Terraform import is pending):**

```powershell
$RG = "rg-skatebot-prod"
$FA = "skatebot-prod-azure-32441"
$SA = (az functionapp config appsettings list --name $FA --resource-group $RG `
        --query "[?name=='AzureWebJobsStorage'].value | [0]" -o tsv `
        | Select-String -Pattern "AccountName=([^;]+)" -AllMatches).Matches.Groups[1].Value

# 1. Container
az storage container create --account-name $SA --name attendance --auth-mode login

# 2. App settings
az functionapp config appsettings set --name $FA --resource-group $RG --settings `
  "BLOB_ACCOUNT_URL=https://$SA.blob.core.windows.net" "ATTENDANCE_CONTAINER=attendance"

# 3. RBAC: Blob Data Contributor on the FA's MI
$MI_PRINCIPAL = (az functionapp identity show --name $FA --resource-group $RG --query principalId -o tsv)
$SA_ID = (az storage account show --name $SA --resource-group $RG --query id -o tsv)
az role assignment create --assignee-object-id $MI_PRINCIPAL --assignee-principal-type ServicePrincipal `
  --role "Storage Blob Data Contributor" --scope $SA_ID

# 4. Cosmos data role on the local user (so build_attendance_report.py can query)
$COSMOS_ACCT = (az cosmosdb list --resource-group $RG --query "[0].name" -o tsv)
$ME = (az ad signed-in-user show --query id -o tsv)
az cosmosdb sql role assignment create `
  --account-name $COSMOS_ACCT --resource-group $RG `
  --scope "/" `
  --role-definition-id 00000000-0000-0000-0000-000000000002 `
  --principal-id $ME

# 5. Redeploy Function App with the new exporter code
cd C:\UniPain\skatetelegrambot-aws\azure\functionapp
func azure functionapp publish $FA --python
```

### Smoke test after rollout

To re-trigger the exporter on an already-tested session without waiting for the next live one:

1. Open Azure Portal → Cosmos DB → Data Explorer → `sessions` table → find the test session row → set `exported = 0`, save.
2. Enqueue a fresh Storage Queue message (PowerShell):

   ```powershell
   $SESSION_ID = "<session-ulid>"  # the row you just reset
   $payload = @{ session_id = $SESSION_ID } | ConvertTo-Json -Compress
   $b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($payload))
   az storage message put --queue-name export-queue --content $b64 --account-name $SA --auth-mode login --visibility-timeout 5
   ```

3. Wait ~60s, then in Azure Portal → Storage Account → Containers → `attendance` confirm `attendance/{date}_{ulid}.csv` exists. Download and verify columns.
4. Run the local script to generate Excel from the same data:

   ```powershell
   python C:\UniPain\skatetelegrambot-aws\azure\scripts\build_attendance_report.py --since 2026-01-01
   start Attendance.xlsx
   ```

### Files changed in this pivot

- `azure/functionapp/exporter/__init__.py` — Power Automate POST removed; Blob CSV upload added
- `azure/functionapp/shared/config.py` — `BLOB_ACCOUNT_URL` + `ATTENDANCE_CONTAINER` env vars
- `azure/functionapp/shared/db.py` — `list_sessions_since` helper added
- `azure/functionapp/requirements.txt` — `azure-storage-blob` added
- `azure/infra/terraform/functionapp.tf` — `attendance` container + new app settings
- `azure/infra/terraform/rbac.tf` — Storage Blob Data Contributor for FA MI; Cosmos data role for the running user
- `azure/scripts/build_attendance_report.py` — new local report generator

### Dormant resources (kept intentionally)

- `power-automate-url` Key Vault secret (placeholder value): unread by code now, but left in Terraform/Vault to avoid a Key Vault destroy-recreate. If a future deployment moves to a Premium tenant, the same secret slot can be re-populated and a one-line revert restores the POST path.

---

## Session Log: B1 Pivot Rollout (2026-04-30 / 2026-05-01)

The exact sequence executed during this session, captured for reproducibility / disaster recovery / portfolio narrative. Run in PowerShell on Windows; variables persist within one window only (see error #10).

### Phase 1 — Setup variables

```powershell
$RG = "rg-skatebot-prod"
$FA = "skatebot-prod-azure-32441"
$conn = az functionapp config appsettings list --name $FA --resource-group $RG `
          --query "[?name=='AzureWebJobsStorage'].value | [0]" -o tsv
if ($conn -match 'AccountName=([^;]+)') { $SA = $matches[1] }
"Storage Acct  : $SA"
```

Result: `$SA = skatebotprod36863`

### Phase 2 — Create the attendance Blob container

```powershell
az storage container create --account-name $SA --name attendance --auth-mode login
```

Result: `{"created": true}` — container ready.

### Phase 3 — Set Function App app settings

```powershell
az functionapp config appsettings set --name $FA --resource-group $RG --settings `
  "BLOB_ACCOUNT_URL=https://$SA.blob.core.windows.net" `
  "ATTENDANCE_CONTAINER=attendance"
```

Result: app settings list dump includes the two new entries; FA restarts (~10s).

Verification:

```powershell
az functionapp config appsettings list --name $FA --resource-group $RG `
  --query "[?name=='BLOB_ACCOUNT_URL' || name=='ATTENDANCE_CONTAINER'].{name:name, value:value}" -o table
```

```
Name                  Value
--------------------  -----------------------------------------------
BLOB_ACCOUNT_URL      https://skatebotprod36863.blob.core.windows.net
ATTENDANCE_CONTAINER  attendance
```

### Phase 4 — Grant Storage Blob Data Contributor to the FA's MI

```powershell
$MI_PRINCIPAL = (az functionapp identity show --name $FA --resource-group $RG --query principalId -o tsv)
$SA_ID = (az storage account show --name $SA --resource-group $RG --query id -o tsv)
az role assignment create --assignee-object-id $MI_PRINCIPAL --assignee-principal-type ServicePrincipal --role "Storage Blob Data Contributor" --scope $SA_ID
```

Result: role assignment ID `6d752efc-6c37-43f7-a0a2-71877ec86776`. MI principalId `042969c3-2a1f-4e8e-b3d6-188010084984`.

> **Footgun caught here**: the `--name $FA --resource-group $RG --query principalId -o tsv` chain wrapped during paste, so PowerShell saw a real newline after `-o` and parsed the rest as separate commands. `$MI_PRINCIPAL` was empty until re-run on a single line. Always collapse `az` invocations to one line when pasting from a long doc.

### Phase 5 — Grant Cosmos DB data role to the running user (your AAD identity)

Without this, `build_attendance_report.py` 403s on Cosmos reads — the FA's MI has the role, but your user does not by default.

```powershell
$COSMOS_ACCT = (az cosmosdb list --resource-group $RG --query "[0].name" -o tsv)
$ME = (az ad signed-in-user show --query id -o tsv)
az cosmosdb sql role assignment create --account-name $COSMOS_ACCT --resource-group $RG --scope "/" --role-definition-id 00000000-0000-0000-0000-000000000002 --principal-id $ME
```

Result: SQL role assignment ID `dc32fe6e-8ae2-4d23-9cd3-f9512227c98d`. Your principalId `b1935c29-1bdd-413f-89cb-d4b784108014`.

### Phase 6 — Grant yourself Storage Queue + Blob roles (for `az storage message put` and `az storage blob list`)

Discovered necessary mid-smoke-test when `--auth-mode login` returned 403 — control-plane access doesn't imply data-plane access.

```powershell
az role assignment create --assignee-object-id $ME --assignee-principal-type User --role "Storage Queue Data Contributor" --scope $SA_ID
az role assignment create --assignee-object-id $ME --assignee-principal-type User --role "Storage Blob Data Contributor" --scope $SA_ID
```

Both succeeded; AAD propagation took ~15s.

### Phase 7 — Redeploy Function App with the new exporter code

```powershell
cd C:\UniPain\skatetelegrambot-aws\azure\functionapp
func azure functionapp publish $FA --python
```

Build steps observed (~3 min):
1. Local zip created
2. Uploaded to SCM endpoint
3. Oryx remote build with Python 3.12.13
4. `pip install` from `requirements.txt` — `azure-storage-blob 12.28.0` installed for the first time (and all other deps unchanged)
5. squashfs artifact built (12.05 MB)
6. Workers reset; functions list confirms all three triggers present:

```
Functions in skatebot-prod-azure-32441:
    exporter - [queueTrigger]
    scheduler - [timerTrigger]
    webhook - [httpTrigger]
        Invoke url: https://skatebot-prod-azure-32441.azurewebsites.net/api/webhook
```

> Local-vs-deployed Python version warning (`3.13.0` local vs `Python|3.12` deployed) is informational only — Oryx ran the build with 3.12 on its side. Local mismatch only matters if you run `func start` locally.

### Phase 8 — Smoke test: cloud side

1. Cosmos Data Explorer → `sessions` table → pick a row (used `01KQ8YWMBT0W28EDM7TR1XCKE8`, the most recent test session) → **Edit Entity** → set `exported = 0` → Update.

2. Enqueue an export message:

   ```powershell
   $SESSION_ID = "01KQ8YWMBT0W28EDM7TR1XCKE8"
   $payload = @{ session_id = $SESSION_ID } | ConvertTo-Json -Compress
   $b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($payload))
   az storage message put --queue-name export-queue --content $b64 --account-name $SA --auth-mode login --visibility-timeout 5
   ```

3. Wait ~30s, then list blobs:

   ```powershell
   az storage blob list --account-name $SA --container-name attendance --auth-mode login `
     --query "[].{name:name, size:properties.contentLength, modified:properties.lastModified}" -o table
   ```

   Result: 4 CSVs in container — three from queue messages that had been hidden in the queue with stale 24h visibility timeouts and which the new exporter drained on first run, plus the one from this test:

   ```
   2026-04-28_01KQ8XHZNYKMEPTB9J39S8WD1N.csv  131  2026-04-29T10:30:42+00:00
   2026-04-28_01KQ8XK9PSWG6RXFZFA80G8M5H.csv  131  2026-04-29T10:30:42+00:00
   2026-04-28_01KQ8YBVR7ATXKH6QKND8N96PJ.csv  131  2026-04-29T10:30:42+00:00
   2026-04-28_01KQ8YWMBT0W28EDM7TR1XCKE8.csv  131  2026-04-30T18:09:14+00:00   ← this test
   ```

   The same-second timestamps on the first three confirm Storage Queue's at-least-once delivery + the exporter's idempotent `exported`-flag design caught up automatically when the new code came online — exactly as designed.

### Phase 9 — Smoke test: local side

```powershell
pip install openpyxl azure-data-tables azure-identity azure-keyvault-secrets   # one-time
python C:\UniPain\skatetelegrambot-aws\azure\scripts\build_attendance_report.py --since 2026-04-01
```

Output:

```
  skip 2026-04-28 01KQ8XHZNYKMEPTB9J39S8WD1N — no responses.
  skip 2026-04-28 01KQ8XK9PSWG6RXFZFA80G8M5H — no responses.
  skip 2026-04-28 01KQ8YBVR7ATXKH6QKND8N96PJ — no responses.
  skip 2026-04-28 01KQ8YWMBT0W28EDM7TR1XCKE8 — no responses.
Wrote C:\UniPain\skatetelegrambot-aws\azure\functionapp\Attendance.xlsx: 1/5 sessions with attendance, 1 attendee-rows.
```

Excel opened cleanly without an import wizard (UTF-8 BOM working). 1 session had a real button-tap recorded (`01KQ8RSD5B066HMZF7Q3J0CK1Y` — the very first end-to-end test from way back); the other 4 were queue/exporter shape tests with no responses.

> **Footgun caught**: original install line missed `azure-keyvault-secrets`. `shared/config.py` does a top-level `from azure.keyvault.secrets import SecretClient` even though the local script never reads from Key Vault. Cheaper to install the dep than to refactor config.py into Key-Vault-optional layers. Fixed in commit `43a1dbb`.

### Phase 10 — Commit, push, PR, merge

1. Pivot commit `f64207e` — 12 files (10 pivot + `out.json` + `scripts/bootstrap-ssm.ps1` notes), pushed
2. Follow-up commit `43a1dbb` — `.gitignore` for generated reports, missing pip dep, smoke-test green note
3. PR #1 opened via `gh pr create --base main --head feat/azure-port` — body covered summary, mapping table, verification, follow-ups
4. PR merged via `gh pr merge 1 --merge` (branch preserved on origin) — merge commit `1fa5ad6`, fast-forward of 40 files / +3,638 / −95 lines onto `main`

Local `main` synced via `git pull origin main`.

### Final state at end of session

| | |
|---|---|
| Repo branch | `main` |
| Last commit | `1fa5ad6` (Merge pull request #1) |
| Function App | `skatebot-prod-azure-32441` running B1 exporter (Blob CSV upload) |
| Storage container | `attendance` exists, 4 CSVs present |
| Telegram webhook | Pointed at Azure (`https://skatebot-prod-azure-32441.azurewebsites.net/api/webhook`) |
| Local Excel report | `azure/functionapp/Attendance.xlsx` generated, gitignored |
| AWS deployment | Resources exist (M4) but SSM unpopulated — M5 unblocked, deferred |
| AWS exporter pivot | Deferred to `feat/aws-export-pivot` off main |
