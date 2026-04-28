provider "azurerm" {
  features {
    key_vault {
      # Don't auto-purge soft-deleted vaults on destroy. Safer default given
      # Key Vault's 7–90 day soft-delete retention; users who genuinely want
      # to recreate must purge manually.
      purge_soft_delete_on_destroy    = false
      recover_soft_deleted_key_vaults = true
    }
    resource_group {
      prevent_deletion_if_contains_resources = false
    }
  }
}

provider "random" {}

data "azurerm_client_config" "current" {}
