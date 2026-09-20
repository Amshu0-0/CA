# This file creates the SSH key pair that will be used
# to connect to the EC2 instances.
#
# Terraform creates the key pair and registers the public key with AWS.
# Ansible will later use the private key file to SSH into the machines.


# Generate a new RSA SSH key pair.
#
# The public key will be sent to AWS.
# The private key will stay on my local computer and will be used by Ansible when connecting to the EC2 instances.
resource "tls_private_key" "ca1" {
  algorithm = "RSA"
  rsa_bits  = 4096
}


# Register the generated public key with AWS.
#
# EC2 instances created later will reference "ca1-key".
# AWS only receives the public key, not the private key.
resource "aws_key_pair" "ca1" {
  key_name   = "ca1-key"
  public_key = tls_private_key.ca1.public_key_openssh
}


# Save the private SSH key on my local computer.
#
# Ansible will use this .pem file to SSH into the EC2 instances.
# file_permission = "0400" means only my user account can read the file.
resource "local_sensitive_file" "private_key" {
  content         = tls_private_key.ca1.private_key_pem
  filename        = abspath("${path.module}/ca1-key.pem")
  file_permission = "0400"
}