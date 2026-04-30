output "webhook_function_url" {
  description = "Public URL of the webhook function. Register this with Telegram via setwebhook.py."
  value       = "https://${azurerm_linux_function_app.main.default_hostname}/api/webhook"
}

output "function_app_name" {
  description = "Function App name. Use with `func azure functionapp publish <name> --python --build remote`."
  value       = azurerm_linux_function_app.main.name
}

output "cosmos_account_name" {
  description = "Cosmos DB account name. Open Data Explorer at portal.azure.com → this account → Data Explorer."
  value       = azurerm_cosmosdb_account.main.name
}

output "cosmos_endpoint" {
  description = "Cosmos DB Table API endpoint URL."
  value       = azurerm_cosmosdb_account.main.endpoint
}

output "key_vault_name" {
  description = "Key Vault name. Inspect with `az keyvault secret list --vault-name <name>`."
  value       = azurerm_key_vault.main.name
}

output "key_vault_uri" {
  description = "Key Vault URI."
  value       = azurerm_key_vault.main.vault_uri
}

output "storage_account_name" {
  description = "Storage Account hosting AzureWebJobsStorage + the export-queue."
  value       = azurerm_storage_account.main.name
}

output "application_insights_name" {
  description = "Application Insights resource name. Open Logs blade for KQL queries."
  value       = azurerm_application_insights.main.name
}

output "resource_group_name" {
  description = "Resource Group containing the entire stack."
  value       = azurerm_resource_group.main.name
}
