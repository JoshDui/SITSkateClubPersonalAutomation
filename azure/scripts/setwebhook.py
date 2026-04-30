"""
Register the Azure Function URL as the active Telegram webhook.

Reads:
  - Function URL from Terraform output `webhook_function_url`
  - webhook_secret + bot_token from Azure Key Vault

TODO: Implement in milestone A4.
"""
