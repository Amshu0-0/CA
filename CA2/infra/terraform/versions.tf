# Terraform and provider versions, pinned so every computer builds the same thing.
# "~> 6.0" means any 6.x release but never 7.0. After the first "terraform init", commit the generated
# .terraform.lock.hcl file: it records the exact provider builds that were used.
terraform {
  required_version = ">= 1.6.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    http = {
      source  = "hashicorp/http"
      version = "~> 3.4"
    }
  }
}

# The region and the tags come from config.yml.
# default_tags puts the same tags on every AWS resource that Terraform creates.
# The Project tag is what the cleanup scan looks for after "make cluster-down".
provider "aws" {
  region = local.config.region

  default_tags {
    tags = local.tags
  }
}
