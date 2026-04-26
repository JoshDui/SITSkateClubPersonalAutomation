"""
Runtime configuration — env vars + Azure Key Vault secret reads.

Mirrors the public function signatures of the AWS shared/config.py so handler
code stays portable.

TODO: Implement in milestone A3.
Replaces:
    boto3.client("ssm")  → azure.keyvault.secrets.SecretClient
                           authenticated with azure.identity.DefaultAzureCredential
                           (uses Managed Identity in prod, az CLI auth locally)
"""
from __future__ import annotations
