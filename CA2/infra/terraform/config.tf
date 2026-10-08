# Read the shared settings from infra/config.yml and work out the values that depend on them.
locals {
  config = yamldecode(file("${path.module}/../config.yml"))
  name   = local.config.name_prefix

  tags = {
    Project    = local.config.project_tag
    Course     = "CS5287"
    Assignment = "CA2"
    ManagedBy  = "terraform"
  }

  # "auto" asks the Internet for this computer's public address. Otherwise the range from config.yml is used.
  admin_cidr = local.config.admin_cidr == "auto" ? "${chomp(data.http.my_ip[0].response_body)}/32" : local.config.admin_cidr
  rest_cidr  = local.config.rest_api_cidr == "admin" ? local.admin_cidr : local.config.rest_api_cidr

  zone = local.config.availability_zone != "" ? local.config.availability_zone : data.aws_availability_zones.available[0].names[0]
  ami  = local.config.ami_id != "" ? local.config.ami_id : data.aws_ami.ubuntu[0].id
}

# This computer's public IP address as the Internet sees it. Used only when admin_cidr is "auto".
data "http" "my_ip" {
  count = local.config.admin_cidr == "auto" ? 1 : 0
  url   = "https://checkip.amazonaws.com"

  lifecycle {
    postcondition {
      condition     = self.status_code == 200
      error_message = "Could not look up this computer's public IP address. Set admin_cidr in config.yml to a fixed range written as an IP address followed by /32."
    }
  }
}

# Ask AWS which Availability Zones the region offers, so no zone name is ever hardcoded.
# Skipped when config.yml names a zone.
data "aws_availability_zones" "available" {
  count = local.config.availability_zone == "" ? 1 : 0
  state = "available"
}

# The newest official Ubuntu 24.04 LTS image from Canonical (AWS account 099720109477),
# unless config.yml pins an ami_id.
data "aws_ami" "ubuntu" {
  count       = local.config.ami_id == "" ? 1 : 0
  most_recent = true
  owners      = ["099720109477"]

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}
