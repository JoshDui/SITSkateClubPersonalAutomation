# Azure Deployment Progress

**Branch:** `feat/azure-port` (off `main`)
**Last commit:** `062417d` — Route /sendpoll confirmation to the same topic as the poll
**Last updated:** 2026-04-29
**Status:** Export pivot **code complete, uncommitted, not yet rolled out**. End-to-end Azure pipeline (Telegram → webhook → Cosmos → queue → exporter) is verified working from prior sessions. Pivot replaces the dead Power Automate sink with Blob CSV + a local openpyxl script (see "Attendance Export Pivot" below). All four edited Python files compile; `terraform validate` passes.

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
| B1 | **Export pivot** (Power Automate → Blob CSV + local openpyxl) | 🟢 Code complete (this session), uncommitted, rollout + smoke test pending |

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

## Current Blocker

**Pending verification** — the synthetic-test ID issue (error #15) was identified. The next smoke test should use real `admin-ids` and `group-chat-id` values fetched from Key Vault. See "Resume Cold" step 5 for the corrected PowerShell block.

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

6. **Once webhook is green**, smoke-test scheduler (manual timer trigger) and exporter (enqueue with short visibility timeout). Then move to A7 (Terraform IaC).

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
pip install openpyxl azure-data-tables azure-identity
```

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
