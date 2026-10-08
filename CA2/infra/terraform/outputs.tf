# What the other tools and the person running this need to know about the cluster that now exists.

output "server_public_ip" {
  description = "Public address of the k3s server (control plane)"
  value       = aws_instance.server.public_ip
}

output "agent_public_ips" {
  description = "Public addresses of the k3s agents (workers)"
  value       = aws_instance.agent[*].public_ip
}

output "admin_cidr_in_use" {
  description = "The only address range that may reach SSH and the Kubernetes API"
  value       = local.admin_cidr
}

output "ami_in_use" {
  description = "The machine image the nodes were created from"
  value       = local.ami
}

output "vpc_id" {
  description = "The network created for this cluster"
  value       = aws_vpc.main.id
}

# The Ansible inventory. "make cluster-up" saves this text to infra/ansible/inventory.ini.
output "ansible_inventory" {
  description = "Inventory file for Ansible, built from the machines Terraform created"
  value = templatefile("${path.module}/inventory.tpl", {
    server = {
      name       = "${local.name}-server"
      public_ip  = aws_instance.server.public_ip
      private_ip = aws_instance.server.private_ip
    }
    agents = [for i, a in aws_instance.agent : {
      name       = "${local.name}-agent-${i + 1}"
      public_ip  = a.public_ip
      private_ip = a.private_ip
    }]
    user     = local.config.ssh_user
    key_file = abspath("${path.module}/../../.secrets/ca2-key")
  })
}
