# Find the most recent Ubuntu AMI automatically.
# This avoids hardcoding one specific AMI ID, since AMI IDs
# can be different depending on the AWS region.

data "aws_ami" "ubuntu" {
  most_recent = true

  # Canonical's AWS account ID.
  # This makes sure the image comes from the official Ubuntu publisher.
  owners = ["099720109477"]

  # Find the Ubuntu 26.04 server image that uses gp3 storage.
  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd-gp3/ubuntu-*-26.04-amd64-server-*"]
  }

  # Only use AMIs that support HVM virtualization.
  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}


# Define the four VMs used in the CA1 pipeline.
# The key is the VM role and the value is a short description of what that machine will run.
locals {
  vms = {
    producer  = "Producer container"
    broker    = "Kafka (KRaft)"
    processor = "Processor container"
    database  = "MongoDB + REST API"
  }
}


# Create one EC2 instance for every entry in local.vms.
# Using for_each avoids writing four almost identical aws_instance blocks.

resource "aws_instance" "vm" {

  # Run this resource once for producer, broker, processor, and database.
  for_each = local.vms

  # Use the latest Ubuntu AMI found above.
  ami = data.aws_ami.ubuntu.id

  # Use the instance size defined in variables.tf.
  instance_type = var.instance_type

  # Use the SSH key pair created in key_pair.tf.
  key_name = aws_key_pair.ca1.key_name

  # Launch the VM inside the subnet found in network.tf.
  subnet_id = data.aws_subnet.chosen.id

  # Attach the shared CA1 security group from security.tf.
  vpc_security_group_ids = [aws_security_group.ca1.id]

  # Give each VM a public IP so Ansible and I can connect to it.
  associate_public_ip_address = true


  # Give each instance a readable name and role in AWS.
  tags = {
    # Creates names such as producer-vm, broker-vm, processor-vm, and database-vm.
    Name = "${each.key}-vm"

    # Uses the description from the local.vms map above.
    Role = each.value
  }
}