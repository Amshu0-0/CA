# Read the shared pipeline settings from CA1/config.yml.
# Ansible reads the same file, so Terraform and Ansible always agree on ports and names.
locals {
  config = yamldecode(file("${path.module}/../config.yml"))
}
