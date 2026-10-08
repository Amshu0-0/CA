# The firewall around the three machines (an AWS security group). Nothing is open except what is listed here.
# Security group descriptions only accept letters, digits, spaces and . _ - : / ( ) # , @ [ ] + = & ; { } ! $ *
# so none of the text below uses apostrophes or quotation marks.

locals {
  # What the administrator's computer may reach.
  from_admin = {
    ssh      = { port = 22, cidr = local.admin_cidr, note = "SSH, used by Ansible and for debugging" }
    kube_api = { port = 6443, cidr = local.admin_cidr, note = "Kubernetes API, used by kubectl on the admin computer" }
    rest_api = { port = local.config.rest_node_port, cidr = local.rest_cidr, note = "REST API node port: the one application port that is published" }
  }

  # What the three machines may send to each other. The source is this same security group, never an address.
  between_nodes = {
    kube_api = { protocol = "tcp", from = 6443, to = 6443, note = "agents talk to the Kubernetes API on the server" }
    kubelet  = { protocol = "tcp", from = 10250, to = 10250, note = "the control plane and metrics-server read each node kubelet" }
    flannel  = { protocol = "udp", from = 8472, to = 8472, note = "pod-to-pod traffic between nodes (VXLAN overlay network)" }
    ping     = { protocol = "icmp", from = -1, to = -1, note = "ping, for troubleshooting" }
  }
}

resource "aws_security_group" "cluster" {
  name        = "${local.name}-cluster"
  description = "CA2 k3s cluster: admin access from one address, node-to-node traffic only inside the group"
  vpc_id      = aws_vpc.main.id

  tags = { Name = "${local.name}-cluster-sg" }

  lifecycle {
    precondition {
      condition     = can(cidrnetmask(local.admin_cidr)) && can(cidrnetmask(local.rest_cidr))
      error_message = "admin_cidr and rest_api_cidr in config.yml must be IPv4 ranges written as an address followed by a slash and a number, or the word auto (admin_cidr) or admin (rest_api_cidr)."
    }
  }
}

resource "aws_vpc_security_group_ingress_rule" "from_admin" {
  for_each = local.from_admin

  security_group_id = aws_security_group.cluster.id
  cidr_ipv4         = each.value.cidr
  ip_protocol       = "tcp"
  from_port         = each.value.port
  to_port           = each.value.port
  description       = each.value.note

  tags = { Name = "${local.name}-${each.key}" }
}

resource "aws_vpc_security_group_ingress_rule" "between_nodes" {
  for_each = local.between_nodes

  security_group_id            = aws_security_group.cluster.id
  referenced_security_group_id = aws_security_group.cluster.id
  ip_protocol                  = each.value.protocol
  from_port                    = each.value.from
  to_port                      = each.value.to
  description                  = each.value.note

  tags = { Name = "${local.name}-${each.key}" }
}

resource "aws_vpc_security_group_egress_rule" "all_out" {
  security_group_id = aws_security_group.cluster.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
  description       = "Outbound: image pulls, package downloads and the k3s installer"

  tags = { Name = "${local.name}-all-out" }
}
