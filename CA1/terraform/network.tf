# CA0 used the AWS account's default VPC.
# Instead of hardcoding a VPC ID, Terraform looks it up automatically.
# This makes the setup work on a different AWS account too.
data "aws_vpc" "default" {
  default = true
}


# Get all subnets that belong to the default VPC.
# The default VPC usually has multiple subnets in different availability zones.
data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}


# Select one subnet from the default VPC.
# The EC2 instances will be launched inside this subnet.
data "aws_subnet" "chosen" {
  id = data.aws_subnets.default.ids[0]
}