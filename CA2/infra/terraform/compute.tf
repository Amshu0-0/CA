# The three machines: one k3s server (control plane) and agent_count agents (workers).
# They are identical hardware. Ansible decides which role each one plays.

# The public half of the SSH key made by "make cluster-key". The private half never leaves .secrets/.
resource "aws_key_pair" "cluster" {
  key_name   = "${local.name}-key"
  public_key = file("${path.module}/../../.secrets/ca2-key.pub")
}

resource "aws_instance" "server" {
  ami                         = local.ami
  instance_type               = local.config.instance_type
  subnet_id                   = aws_subnet.nodes.id
  vpc_security_group_ids      = [aws_security_group.cluster.id]
  key_name                    = aws_key_pair.cluster.key_name
  associate_public_ip_address = true

  # The disk is encrypted and is deleted together with the machine, so nothing is left behind.
  root_block_device {
    volume_size           = local.config.root_volume_gb
    volume_type           = "gp3"
    encrypted             = true
    delete_on_termination = true
  }

  # IMDSv2 only: a program on the machine must present a token to read its instance metadata.
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }

  volume_tags = merge(local.tags, { Name = "${local.name}-server-root" })
  tags        = { Name = "${local.name}-server", Role = "k3s-server" }

  # A newer Ubuntu image must not make the next plan want to replace a running machine.
  lifecycle {
    ignore_changes = [ami]
  }
}

resource "aws_instance" "agent" {
  count = local.config.agent_count

  ami                         = local.ami
  instance_type               = local.config.instance_type
  subnet_id                   = aws_subnet.nodes.id
  vpc_security_group_ids      = [aws_security_group.cluster.id]
  key_name                    = aws_key_pair.cluster.key_name
  associate_public_ip_address = true

  root_block_device {
    volume_size           = local.config.root_volume_gb
    volume_type           = "gp3"
    encrypted             = true
    delete_on_termination = true
  }

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }

  volume_tags = merge(local.tags, { Name = "${local.name}-agent-${count.index + 1}-root" })
  tags        = { Name = "${local.name}-agent-${count.index + 1}", Role = "k3s-agent" }

  lifecycle {
    ignore_changes = [ami]
  }
}
