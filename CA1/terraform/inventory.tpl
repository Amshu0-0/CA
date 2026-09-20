# This is an Ansible inventory template.
# Terraform will replace each value with the real IP address or file path after the infrastructure has been created.

# Producer VM.
# Ansible will connect using the producer's public IP, the Ubuntu user, and the SSH private key created by Terraform.
[producer]
${producer_ip} ansible_user=ubuntu ansible_ssh_private_key_file=${key_path}

# Broker VM.
# The public IP is used by Ansible to SSH into the machine.
# The private IP is also saved because the other pipeline components will use it to reach Kafka inside AWS.
[broker]
${broker_ip} ansible_user=ubuntu ansible_ssh_private_key_file=${key_path} private_ip=${broker_private_ip}

# Processor VM.
# Ansible will use the processor's public IP to connect and configure the machine.
[processor]
${processor_ip} ansible_user=ubuntu ansible_ssh_private_key_file=${key_path}

# Database VM.
# The public IP is used for Ansible SSH access.
# The private IP is saved because the application will use it to reach MongoDB and the REST API inside AWS.
[database]
${database_ip} ansible_user=ubuntu ansible_ssh_private_key_file=${key_path} private_ip=${database_private_ip}

# Variables shared by every machine in the inventory.
# StrictHostKeyChecking is disabled so Ansible can connect to newly created EC2 instances without stopping for a confirmation prompt.
#
# The broker and database private IPs are also shared here so the playbooks can use them when configuring the pipeline.
[all:vars]
ansible_ssh_common_args='-o StrictHostKeyChecking=no'
broker_private_ip=${broker_private_ip}
database_private_ip=${database_private_ip}