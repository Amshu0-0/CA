# This file contains values that may need to change between deployments.
# Using variables prevents values from being hard-coded throughout the Terraform configuration.


# AWS region where the four EC2 instances will be created.
# CA0 used the Ohio region, so CA1 uses the same region by default.
variable "aws_region" {
  description = "AWS region - CA0 used us-east-2 (Ohio)"
  type        = string
  default     = "us-east-2"
}


# EC2 instance size used for all four machines.
# Keeping this as a variable makes it easy to change the size later without editing every EC2 resource individually.
variable "instance_type" {
  description = "EC2 instance type for all four VMs - CA0 used t3.medium"
  type        = string
  default     = "t3.medium"
}


# Only my current public IP will be allowed to SSH into the EC2 machines.
# /32 means exactly one IP address instead of opening SSH to everyone.
# This variable intentionally has no default because each person running the project should provide their own IP.
variable "my_ip_cidr" {
  description = "Your own public IP in CIDR form, for example 68.62.181.203/32"
  type        = string
}