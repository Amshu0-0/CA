# Create one shared security group for all four CA1 VMs.
# This controls what traffic is allowed into and out of the machines.
# It is attached to the default VPC that was found in network.tf.

resource "aws_security_group" "ca1" {
  name        = "ca1-pipeline-sg"
  description = "CA1 - one shared security group for all four pipeline VMs"
  vpc_id      = data.aws_vpc.default.id


  # Allow SSH only from my own public IP.
  # This lets me connect to the EC2 machines without opening SSH to everyone on the internet.
  ingress {
    description = "SSH - operator only"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = [var.my_ip_cidr]
  }


  # Allow access to the REST API only from my own public IP.
  # This keeps port 8080 from being open to the entire internet.
  ingress {
    description = "REST API - operator only"
    from_port   = 8080
    to_port     = 8080
    protocol    = "tcp"
    cidr_blocks = [var.my_ip_cidr]
  }


  # Allow Kafka traffic between the four VMs.
  # self = true means only machines using this same security group can communicate on Kafka port 9092.
  ingress {
    description = "Kafka - VM-to-VM only"
    from_port   = 9092
    to_port     = 9092
    protocol    = "tcp"
    self        = true
  }


  # Allow MongoDB traffic between the four VMs.
  # MongoDB is not exposed directly to the public internet.
  ingress {
    description = "MongoDB - VM-to-VM only"
    from_port   = 27017
    to_port     = 27017
    protocol    = "tcp"
    self        = true
  }


  # Allow the VMs to make outbound connections.
  # This is needed for things like installing packages and downloading Docker images.
  egress {
    description = "Allow all outbound"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }


  # Give the security group a readable name in the AWS console.
  tags = {
    Name = "ca1-pipeline-sg"
  }
}