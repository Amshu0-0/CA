# This file connects the Terraform part of the project to the Ansible part of the project.
#
# Terraform creates the EC2 instances and already knows their public IPs, private IPs, and SSH key location.
#
# This resource takes those values and writes them into the Ansible inventory file automatically.
#
# Ansible does not need to know how the VMs were created. It only needs the connection information Terraform provides.

resource "local_file" "ansible_inventory" {
  # Save the generated inventory inside the Ansible folder.
  filename = "${path.module}/../ansible/inventory.ini"

  # Fill in inventory.tpl using the values Terraform receives after the EC2 instances have been created.
  content = templatefile("${path.module}/inventory.tpl", {

    # Public IP used by Ansible to connect to the producer VM.
    producer_ip = aws_instance.vm["producer"].public_ip

    # Public IP used by Ansible to connect to the broker VM.
    broker_ip = aws_instance.vm["broker"].public_ip

    # Public IP used by Ansible to connect to the processor VM.
    processor_ip = aws_instance.vm["processor"].public_ip

    # Public IP used by Ansible to connect to the database VM.
    database_ip = aws_instance.vm["database"].public_ip

    # Private IP used by the pipeline to reach Kafka.
    broker_private_ip = aws_instance.vm["broker"].private_ip

    # Private IP used by the pipeline to reach the database.
    database_private_ip = aws_instance.vm["database"].private_ip

    # Path to the SSH private key created in key_pair.tf.
    key_path = local_sensitive_file.private_key.filename
  })
}