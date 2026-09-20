# These outputs do not create any new AWS resources.
# They only show values Terraform already knows after the EC2 instances have been created.

# Show the public IP address for each VM.
# These addresses can be used when connecting to a VM from my own computer, such as with SSH.
output "public_ips" {
  description = "Public IP address of each CA1 VM"

  value = {
    for name, vm in aws_instance.vm :
    name => vm.public_ip
  }
}

# Show the private IP address for each VM.
# The pipeline components will use these addresses when communicating with each other inside AWS.
output "private_ips" {
  description = "Private IP address of each CA1 VM"

  value = {
    for name, vm in aws_instance.vm :
    name => vm.private_ip
  }
}

# Give an example SSH command for connecting to the broker VM.
# Terraform automatically fills in the location of the private key and the broker VM's public IP address.
output "ssh_command_example" {
  description = "SSH command for connecting to the broker VM"

  value = "ssh -i ${local_sensitive_file.private_key.filename} ubuntu@${aws_instance.vm["broker"].public_ip}"
}