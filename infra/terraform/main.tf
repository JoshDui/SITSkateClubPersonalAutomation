terraform {
  required_version = ">= 1.7.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.70"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
  }

  # Local state for v1. Migrate to S3 + DynamoDB lock table later if
  # working across multiple machines or in a team.
  # backend "s3" { bucket = "skatebot-tfstate"; key = "prod.tfstate"; region = "ap-southeast-1" }
}

locals {
  project = "skatebot"
  env     = var.env

  # Consistent resource naming: skatebot-prod-webhook, skatebot-prod-members, etc.
  prefix = "${local.project}-${local.env}"

  # Applied to every taggable resource — makes cost allocation trivial.
  common_tags = {
    Project   = local.project
    Env       = local.env
    ManagedBy = "terraform"
    Repo      = "skatetelegrambot-aws"
  }
}
