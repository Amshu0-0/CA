# CA1 now builds its own network instead of borrowing the AWS account's default VPC.
# Everything in this file is created by terraform apply and removed by terraform destroy,
# so the network lives and dies with the rest of the project.

# Ask AWS which Availability Zones are available in the chosen region.
# The subnet below goes in the first one, so no zone name is ever hardcoded.
data "aws_availability_zones" "available" {
  state = "available"
}

# The VPC: a private network that exists only for this project.
# DNS support lets the VMs resolve names, and DNS hostnames gives each VM an AWS hostname.
resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = {
    Name = "ca1-vpc"
  }
}

# The internet gateway: the VPC's door to the internet.
# Without it the VMs could not download Docker, pull images, or accept SSH from my laptop.
resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id

  tags = {
    Name = "ca1-igw"
  }
}

# One public subnet that holds all four VMs.
# map_public_ip_on_launch = true gives every VM in it a public IP automatically.
resource "aws_subnet" "public" {
  vpc_id                  = aws_vpc.main.id
  cidr_block              = var.subnet_cidr
  availability_zone       = data.aws_availability_zones.available.names[0]
  map_public_ip_on_launch = true

  tags = {
    Name = "ca1-public-subnet"
  }
}

# The route table: the subnet's map of where traffic goes.
# The single route sends everything that is not local (0.0.0.0/0) out through the internet gateway.
resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }

  tags = {
    Name = "ca1-public-rt"
  }
}

# Attach the route table to the subnet.
# A route table does nothing until it is associated with a subnet.
resource "aws_route_table_association" "public" {
  subnet_id      = aws_subnet.public.id
  route_table_id = aws_route_table.public.id
}
