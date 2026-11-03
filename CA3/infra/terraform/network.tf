# The cluster builds its own network instead of borrowing the AWS account's default one.
# Everything in this file is created by "make cluster-up" and removed by "make cluster-down".
#
#   Internet --- internet gateway --- route table --- public subnet --- 3 nodes
#
# The nodes sit in a public subnet because they need to pull images and download k3s, and a NAT gateway
# would add cost. The security group (security.tf) decides who can actually reach them.

resource "aws_vpc" "main" {
  cidr_block           = local.config.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = { Name = "${local.name}-vpc" }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id

  tags = { Name = "${local.name}-igw" }
}

resource "aws_subnet" "nodes" {
  vpc_id                  = aws_vpc.main.id
  cidr_block              = local.config.subnet_cidr
  availability_zone       = local.zone
  map_public_ip_on_launch = true

  tags = { Name = "${local.name}-nodes-subnet" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }

  tags = { Name = "${local.name}-public-rt" }
}

resource "aws_route_table_association" "nodes" {
  subnet_id      = aws_subnet.nodes.id
  route_table_id = aws_route_table.public.id
}

# Every VPC comes with a default security group that allows traffic between its members.
# Adopting it with no rules removes all of them, so nothing can use it by accident.
resource "aws_default_security_group" "default" {
  vpc_id = aws_vpc.main.id

  tags = { Name = "${local.name}-default-locked" }
}
