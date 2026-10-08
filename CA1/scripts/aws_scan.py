#!/usr/bin/env python3
"""Cleanup scan: prove that nothing from this project is left in AWS after terraform destroy.

Run it while the project is deployed and it must FIND things, which proves the scan can see them.
Run it after terraform destroy and it must find nothing. It looks at three things:

  1. Everything carrying this project's tag (Project=<project_tag from config.yml>), one resource
     type at a time: instances, VPCs, subnets, security groups, internet gateways, route tables
     and key pairs.
  2. Anything else carrying the tag, in ANY AWS service (the Resource Groups Tagging API).
  3. The resources that cost money, tagged or not: EC2 instances, EBS volumes, Elastic IPs,
     NAT gateways and load balancers. With --all-regions this check covers every enabled region.

Exit code: 0 = nothing is left, 1 = something is left, 2 = the scan itself could not run.

A check that did not run must never look like a clean result. If AWS refuses a call because this
user lacks permission, the scan stops with exit code 2. The only exceptions are two optional checks
(the tag index in 2, and load balancers in 3). They are skipped, shown as [skip ], and named again
in the RESULT line, so the verdict always says exactly what it covers.

It needs only Python 3 and the AWS command-line tool, already signed in. The region and the tag
come from config.yml, the same file Terraform reads, so both always look at the same place.
"""

import argparse
import json
import re
import subprocess
import sys
import time
from functools import partial
from pathlib import Path

CA1_FOLDER = Path(__file__).resolve().parent.parent
CONFIG_FILE = CA1_FOLDER / "config.yml"

# The name of the tag Terraform puts on everything (see default_tags in terraform/versions.tf).
TAG_KEY = "Project"

# Every EC2 instance state except "terminated". Terminated instances stay listed for about an hour
# but are gone, so they are not leftovers.
LIVE_STATES = "pending,running,shutting-down,stopping,stopped"

# How often to look again while AWS's tag index catches up with a deletion.
POLL_SECONDS = 5

# Words in an AWS error that mean "this user is not allowed to do that", as opposed to a real failure.
DENIED_WORDS = ("AccessDenied", "UnauthorizedOperation", "not authorized")


class ScanError(Exception):
    """The scan itself could not run (this is different from finding a leftover resource)."""

    def __init__(self, message, denied=False, action=""):
        super().__init__(message)
        self.denied = denied
        self.action = action


def hide_account_numbers(text):
    """AWS messages and ARNs contain the 12-digit account number. Show only its last four digits."""
    return re.sub(r"\b\d{12}\b", lambda match: "..." + match.group()[-4:], text)


def read_config():
    """Read the simple 'key: value' lines of config.yml."""
    settings = {}
    for line in CONFIG_FILE.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if ":" in line:
            key, value = line.split(":", 1)
            settings[key.strip()] = value.strip()
    return settings


def aws(arguments, region):
    """Run one AWS command-line call and return its JSON answer."""
    command = ["aws", *arguments, "--region", region, "--output", "json"]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=120)
    except FileNotFoundError:
        raise ScanError("the aws command-line tool is not installed")
    except subprocess.TimeoutExpired:
        raise ScanError(f"aws {' '.join(arguments[:2])} did not answer within 120 s")
    if result.returncode != 0:
        lines = result.stderr.strip().splitlines()
        reason = hide_account_numbers(lines[-1]) if lines else "no error text"
        denied = any(word in result.stderr for word in DENIED_WORDS)
        action = re.search(r"not authorized to perform: ([\w:*-]+)", result.stderr)
        raise ScanError(
            f"aws {' '.join(arguments[:2])} failed: {reason}",
            denied,
            action.group(1) if action else "",
        )
    return json.loads(result.stdout) if result.stdout.strip() else {}


def name_of(tags):
    """The value of the Name tag, if there is one."""
    for tag in tags or []:
        if tag.get("Key") == "Name":
            return tag.get("Value", "")
    return ""


def find_instances(region, tag_value=None):
    filters = [f"Name=instance-state-name,Values={LIVE_STATES}"]
    if tag_value:
        filters.append(f"Name=tag:{TAG_KEY},Values={tag_value}")
    found = []
    for reservation in aws(
        ["ec2", "describe-instances", "--filters", *filters], region
    ).get("Reservations", []):
        for instance in reservation.get("Instances", []):
            state = instance["State"]["Name"]
            if state != "terminated":
                found.append(
                    f"{name_of(instance.get('Tags')) or '(no name)'}  "
                    f"{instance['InstanceId']}  ({state})"
                )
    return found


TAGGED_TYPES = [
    ("VPCs", "describe-vpcs", "Vpcs",
     lambda v: f"{v['VpcId']}  ({v.get('CidrBlock', '')})"),
    ("Subnets", "describe-subnets", "Subnets",
     lambda s: f"{s['SubnetId']}  ({s.get('CidrBlock', '')})"),
    ("Security groups", "describe-security-groups", "SecurityGroups",
     lambda g: f"{g['GroupId']}  ({g.get('GroupName', '')})"),
    ("Internet gateways", "describe-internet-gateways", "InternetGateways",
     lambda g: g["InternetGatewayId"]),
    ("Route tables", "describe-route-tables", "RouteTables",
     lambda r: r["RouteTableId"]),
    ("Key pairs", "describe-key-pairs", "KeyPairs",
     lambda k: k["KeyName"]),
]


def find_tagged(region, tag_value, command, list_name, describe):
    data = aws(
        ["ec2", command, "--filters", f"Name=tag:{TAG_KEY},Values={tag_value}"],
        region,
    )
    return [describe(item) for item in data.get(list_name, [])]


def describe_arn(arn):
    """Show 'service  type/id' instead of the full ARN."""
    parts = arn.split(":", 5)
    return f"{parts[2]}  {parts[5]}" if len(parts) == 6 else hide_account_numbers(arn)


def find_tagged_anywhere(region, tag_value, wait):
    """Everything with the tag, in any service."""
    deadline = time.time() + wait
    while True:
        data = aws(
            [
                "resourcegroupstaggingapi",
                "get-resources",
                "--tag-filters",
                f"Key={TAG_KEY},Values={tag_value}",
            ],
            region,
        )
        arns = [
            describe_arn(item["ResourceARN"])
            for item in data.get("ResourceTagMappingList", [])
        ]
        if not arns or time.time() >= deadline:
            return arns
        time.sleep(POLL_SECONDS)


def find_volumes(region):
    data = aws(["ec2", "describe-volumes"], region)
    return [
        f"{v['VolumeId']}  ({v['State']}, {v['Size']} GiB)"
        for v in data.get("Volumes", [])
    ]


def find_addresses(region):
    data = aws(["ec2", "describe-addresses"], region)
    return [
        f"{a.get('AllocationId', '-')}  {a.get('PublicIp', '')}"
        for a in data.get("Addresses", [])
    ]


def find_nat_gateways(region):
    data = aws(["ec2", "describe-nat-gateways"], region)
    return [
        f"{n['NatGatewayId']}  ({n['State']})"
        for n in data.get("NatGateways", [])
        if n["State"] in ("pending", "available")
    ]


def find_load_balancers(region):
    modern = aws(["elbv2", "describe-load-balancers"], region)
    classic = aws(["elb", "describe-load-balancers"], region)
    return (
        [lb["LoadBalancerName"] for lb in modern.get("LoadBalancers", [])]
        + [
            f"{lb['LoadBalancerName']}  (classic)"
            for lb in classic.get("LoadBalancerDescriptions", [])
        ]
    )


BILLABLE = [
    ("EC2 instances", find_instances, False),
    ("EBS volumes", find_volumes, False),
    ("Elastic IP addresses", find_addresses, False),
    ("NAT gateways", find_nat_gateways, False),
    ("Load balancers", find_load_balancers, True),
]


def show(title, found):
    print(
        f"  [{'FOUND' if found else 'none '}] {title}"
        + (f" ({len(found)})" if found else "")
    )
    for item in found:
        print(f"            {item}")
    return len(found)


def look(title, finder, skipped, optional=False):
    """Run one check and print its line."""
    try:
        found = finder()
    except ScanError as error:
        if not (optional and error.denied):
            raise
        print(
            f"  [skip ] {title} "
            f"(permission denied{': ' + error.action if error.action else ''})"
        )
        skipped.append(f"{title} ({error.action})" if error.action else title)
        return 0
    return show(title, found)


def find_billable_elsewhere(region):
    """Billable resources in another region."""
    found, refused = [], []
    for title, finder, _ in BILLABLE:
        try:
            items = finder(region)
        except ScanError as error:
            if not error.denied:
                raise
            refused.append(title)
        else:
            if items:
                found.append((title, items))
    return found, refused


def run_scan(region, tag_value, wait, all_regions):
    identity = aws(["sts", "get-caller-identity"], region)
    print("CA1 cleanup scan")
    print(f"  account : ...{identity['Account'][-4:]}")
    print(f"  region  : {region}")
    print(f"  tag     : {TAG_KEY}={tag_value}")
    leftovers, skipped = 0, []

    print(f"\nCreated by this project (tagged {TAG_KEY}={tag_value})")
    project_total = look(
        "EC2 instances",
        partial(find_instances, region, tag_value),
        skipped,
    )
    for title, command, list_name, describe in TAGGED_TYPES:
        project_total += look(
            title,
            partial(find_tagged, region, tag_value, command, list_name, describe),
            skipped,
        )
    leftovers += project_total

    print("\nAnything else with that tag, in any AWS service")
    wait_now = wait if project_total == 0 else 0
    leftovers += look(
        "Tagged resources",
        partial(find_tagged_anywhere, region, tag_value, wait_now),
        skipped,
        optional=True,
    )

    print(f"\nResources that cost money, tagged or not, in {region}")
    for title, finder, optional in BILLABLE:
        leftovers += look(
            title,
            partial(finder, region),
            skipped,
            optional,
        )

    if all_regions:
        others = sorted(
            r["RegionName"]
            for r in aws(["ec2", "describe-regions"], region).get("Regions", [])
            if r["RegionName"] != region
        )
        print(
            f"\nResources that cost money, in the other "
            f"{len(others)} enabled regions"
        )

        for other in others:
            found, refused = find_billable_elsewhere(other)
            mark = "FOUND" if found else ("skip " if refused else "none ")
            print(
                f"  [{mark}] {other}"
                + (
                    f"  (permission denied: {', '.join(refused)})"
                    if refused
                    else ""
                )
            )

            for title, items in found:
                leftovers += len(items)
                for item in items:
                    print(f"            {title}: {item}")

            if refused:
                skipped.append(f"{other}: {', '.join(refused)}")

    print()

    where = "in any region" if all_regions else f"in {region}"

    if leftovers == 0:
        print(
            f"RESULT: CLEAN - nothing tagged {TAG_KEY}={tag_value} is left, "
            f"and no billable resources are running {where}."
        )
    else:
        print(
            "RESULT: LEFTOVERS FOUND - the resources listed above are still in AWS."
        )

    if skipped:
        print(
            "        Not checked, because this AWS user is not allowed to: "
            f"{'; '.join(skipped)}."
        )

    return 0 if leftovers == 0 else 1


def main():
    parser = argparse.ArgumentParser(
        description="Prove that nothing from this project is left in AWS."
    )
    parser.add_argument(
        "--all-regions",
        action="store_true",
        help="also look for billable resources in every other enabled region",
    )
    parser.add_argument(
        "--wait",
        type=int,
        default=60,
        help="seconds to let AWS's tag index catch up after a deletion (default 60)",
    )
    arguments = parser.parse_args()

    try:
        settings = read_config()
        if not settings.get("aws_region") or not settings.get("project_tag"):
            raise ScanError("config.yml must contain aws_region and project_tag")

        return run_scan(
            settings["aws_region"],
            settings["project_tag"],
            arguments.wait,
            arguments.all_regions,
        )

    except ScanError as error:
        print(f"\nRESULT: COULD NOT SCAN - {error}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
