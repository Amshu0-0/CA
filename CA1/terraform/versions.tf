# This file tells Terraform which version and providers are needed to build the CA1 infrastructure.

terraform {

  # Require Terraform 1.5.0 or newer.
  required_version = ">= 1.5.0"

  required_providers {

    # AWS provider:
    # Used to create AWS resources such as EC2 instances, security groups, and the AWS SSH key pair.
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }

    # TLS provider:
    # Used to generate the SSH key that Ansible will later use to connect to the EC2 instances.
    tls = {
      source  = "hashicorp/tls"
      version = "~> 4.0"
    }

    # Local provider:
    # Allows Terraform to create local files on my Mac. We will use this to save information needed by Ansible.
    local = {
      source  = "hashicorp/local"
      version = "~> 2.0"
    }
  }
}

# Tells Terraform that the infrastructure will be created in AWS.
# The actual region comes from variables.tf instead of being hard-coded here.
provider "aws" {
  region = var.aws_region
}