# Azure Deployment Progress

**Branch:** `feat/azure-port` (off `main`)
**Last commit:** `dcf73fc` — Fix Cosmos Table API rejecting 'id' as reserved property name
**Last updated:** 2026-04-28
**Status:** A4 in progress — webhook deployed, smoke test failing on a new exception (not yet inspected)

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
| A4 | Function App + webhook function | 🟡 **In progress** — deployed, smoke test failing |
| A5 | Scheduler function + Storage Queue | 🟡 Code committed (`00b54ef`), not yet smoke-tested |
| A6 | Exporter function | 🟡 Code committed (`00b54ef`), not yet smoke-tested |
| A7 | Terraform azurerm IaC | ⬜ Pending — `azure/infra/terraform/*.tf` are empty stubs |
| A8 | Migration script + CI/CD | ⬜ Pending — `azure/scripts/migrate_aws_to_azure.py` is a stub |
| A9 | README + multi-cloud framing | ⬜ Pending |
| A10 | End-to-end smoke test | ⬜ Pending — depends on A4 unblock |

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

---

## Current Blocker

After the Cosmos `id` fix (commit `dcf73fc`), redeployed and re-ran the simulated `/sendpoll`:
```
Status: 200, Body: error-logged
```

The webhook handler's outer `except` always returns 200 with body `error-logged` to prevent Telegram retries (which compound failures). The actual exception is in App Insights but **has not yet been inspected** post-`id`-fix.

**Next action when resuming:** Run the App Insights query (error #9 above) to get the new exception. Likely candidates: a different Cosmos schema constraint, a missing app setting, or a bug in `_entity_to_dict` that the prior crash masked.

---

## How to Resume Cold

1. **Verify current state**
   ```powershell
   git -C C:\UniPain\skatetelegrambot-aws status
   git -C C:\UniPain\skatetelegrambot-aws log --oneline feat/azure-port -10
   az account show --query "{name:name, id:id}" -o table
   az resource list --resource-group rg-skatebot-prod --query "[].{name:name,type:type}" -o table
   ```

2. **Set session variables**
   ```powershell
   $RG = "rg-skatebot-prod"
   $APP_NAME = "skatebot-prod-azure-32441"
   $VAULT = "skatebot-prod-kv-29024"
   $URL = "https://$APP_NAME.azurewebsites.net/api/webhook"
   $SECRET = az keyvault secret show --vault-name $VAULT --name webhook-secret --query value -o tsv
   $APP_ID = az monitor app-insights component show --resource-group $RG --query "[0].appId" -o tsv
   ```

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

5. **Re-run smoke test** (see error #9 + the publish-test loop in this doc's history)
   - Direct POST a fake `/sendpoll` update to the webhook URL with the secret header.
   - Expect `200 ok` (not `error-logged`).
   - Verify a row appears in Cosmos `sessions` table (Data Explorer).
   - Verify a poll message appears in the Telegram group.

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
| `azure/infra/terraform/*.tf` | ⬜ Empty stubs — A7 work |
| `azure/scripts/{setwebhook.py, migrate_aws_to_azure.py}` | ⬜ Stubs |
| `azure/tests/*.py` | ⬜ Stubs |
| `azure/README.md` | ⬜ Stub |
