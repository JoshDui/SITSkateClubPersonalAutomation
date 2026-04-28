# Azure Function App + Cosmos DB + Key Vault + Application Insights stack.
# Mirrors the AWS Terraform layout in ../../../infra/terraform/.

terraform {
  required_version = ">= 1.7.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.10"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  # Local state for v1. To migrate to remote azurerm backend, see
  # docs/remote-state.md (TBD) — needs a separate Storage Account + container
  # bootstrapped outside Terraform.
  # backend "azurerm" {
  #   resource_group_name  = "rg-skatebot-tfstate"
  #   storage_account_name = "skatebottfstate"
  #   container_name       = "tfstate"
  #   key                  = "azure.tfstate"
  # }
}

resource "azurerm_resource_group" "main" {
  name     = "rg-${local.prefix}"
  location = var.location
  tags     = local.common_tags
}

# Random suffixes for resources whose names must be globally unique. Generated
# once and persisted in state; on `terraform import` of an existing deployment
# you set these via -var to match the existing names (see import.ps1).
resource "random_integer" "fa_suffix" {
  min     = 10000
  max     = 99999
  keepers = { project = local.project, env = local.env }
}

resource "random_integer" "kv_suffix" {
  min     = 10000
  max     = 99999
  keepers = { project = local.project, env = local.env }
}

resource "random_integer" "sa_suffix" {
  min     = 10000
  max     = 99999
  keepers = { project = local.project, env = local.env }
}

locals {
  project = "skatebot"
  env     = var.env
  prefix  = "${local.project}-${local.env}"

  # Common tags applied to every taggable resource — makes cost-allocation
  # filters trivial in Cost Management.
  common_tags = {
    Project   = local.project
    Env       = local.env
    ManagedBy = "terraform"
    Repo      = "skatetelegrambot-aws"
    Cloud     = "azure"
  }

  # Resolved names — used by the *.tf files that follow. The suffixed names
  # exist because Function App / Storage Account / Key Vault all require
  # globally-unique DNS names.
  fa_name     = coalesce(var.fa_name_override, "${local.prefix}-azure-${random_integer.fa_suffix.result}")
  kv_name     = coalesce(var.kv_name_override, "${local.prefix}-kv-${random_integer.kv_suffix.result}")
  sa_name     = coalesce(var.sa_name_override, "skatebot${local.env}sa${random_integer.sa_suffix.result}")
  cosmos_name = "${local.prefix}-cosmos"
  ai_name     = local.fa_name # App Insights co-named with the Function App
  law_name    = "${local.prefix}-law"
  plan_name   = "${local.prefix}-plan"
  queue_name  = "export-queue"
}
