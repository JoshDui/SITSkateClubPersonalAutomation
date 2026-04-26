# Azure Function App + Cosmos DB + Key Vault + Application Insights
# Mirrors the AWS Terraform layout in ../../../infra/terraform/

terraform {
  required_version = ">= 1.9.0"
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
}

# TODO: Define locals in milestone A7
