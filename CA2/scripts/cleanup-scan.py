#!/usr/bin/env python3
# The cleanup scan: proves that nothing from this project is left in AWS after "make cluster-down".
#
#   make scan                            (the region and the tag come from infra/config.yml, the file Terraform reads)
#   make scan SCAN_ARGS="--all-regions"  (also look for things that cost money in every other region)
#
# Run it while the cluster exists and it must FIND things: that proves the scan can see them.
# Run it after "make cluster-down" and it must find nothing. It looks at three things:
#
#   1. Everything that carries this project's tag (Project=<project_tag>), one kind of resource at a time:
#      machines, disks, networks, subnets, firewalls, gateways, route tables and key pairs.
#   2. Anything else that carries the tag, in ANY AWS service (the Resource Groups Tagging API).
#   3. The resources that cost money, tagged or not: machines, disks, Elastic IPs, NAT gateways and load balancers.
#
# Exit code: 0 = nothing is left, 1 = something is left, 2 = the scan itself could not run.
#
# A check that did not run must never look like a clean result. If AWS refuses a call because this user is not allowed
# to make it, the scan stops with exit code 2. The only exceptions are two optional checks (the tag index in 2, and
# load balancers in 3). They are skipped, shown as [skip ], and named again in the RESULT line, so the verdict always
# says exactly what it covers.
#
# It needs only Python 3 and the AWS command-line tool, already signed in.

import argparse
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from functools import partial
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = ROOT / "infra" / "config.yml"

# The name of the tag that Terraform puts on everything (default_tags in infra/terraform/versions.tf).
# "make verify" checks that Terraform and this scan use the same name.
TAG_KEY = "Project"

# Every machine state except "terminated". Terminated machines stay in the list for about an hour but are gone,
# so they are not leftovers.
LIVE_STATES = "pending,running,shutting-down,stopping,stopped"

# How often to look again while AWS's tag index catches up with a deletion.
POLL_SECONDS = 5

# Words in an AWS error that mean "this user is not allowed to do that", as opposed to a real failure.
DENIED_WORDS = ("AccessDenied", "UnauthorizedOperation", "not authorized")


# The scan itself could not run. This is different from finding a leftover resource.
class ScanError(Exception):
    def __init__(self, message, denied=False, action=""):
        super().__init__(message)
        self.denied = denied
        self.action = action


# AWS messages and ARNs contain the 12-digit account number. Show only its last four digits.
def hide_account_numbers(text):
    return re.sub(r"\b\d{12}\b", lambda match: "..." + match.group()[-4:], text)


# The simple "key: value" lines of infra/config.yml.
def read_config():
    try:
        lines = CONFIG_FILE.read_text().splitlines()
    except OSError as error:
        raise ScanError(f"cannot read {CONFIG_FILE.relative_to(ROOT)}: {error.strerror}")

    settings = {}
    for line in lines:
        line = line.split("#", 1)[0].strip()
        if ":" in line:
            key, value = line.split(":", 1)
            settings[key.strip()] = value.strip().strip("\"'")
    return settings


# One AWS command-line call. Returns its JSON answer.
def aws(arguments, region):
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


# The value of the Name tag, if there is one.
def name_of(tags):
    for tag in tags or []:
        if tag.get("Key") == "Name":
            return tag.get("Value", "")
    return ""


def find_instances(region, tag_value=None):
    filters = [f"Name=instance-state-name,Values={LIVE_STATES}"]
    if tag_value:
        filters.append(f"Name=tag:{TAG_KEY},Values={tag_value}")

    found = []
    for reservation in aws(["ec2", "describe-instances", "--filters", *filters], region).get("Reservations", []):
        for instance in reservation.get("Instances", []):
            state = instance["State"]["Name"]
            if state != "terminated":
                found.append(f"{name_of(instance.get('Tags')) or '(no name)'}  {instance['InstanceId']}  ({state})")
    return found


def describe_volume(volume):
    return f"{name_of(volume.get('Tags')) or '(no name)'}  {volume['VolumeId']}  ({volume['State']}, {volume['Size']} GiB)"


# The kinds of resource that can be found by the tag: (title, command, name of the list in the answer, one line about an item).
TAGGED_TYPES = [
    ("EBS volumes", "describe-volumes", "Volumes", describe_volume),
    ("VPCs", "describe-vpcs", "Vpcs", lambda v: f"{v['VpcId']}  ({v.get('CidrBlock', '')})"),
    ("Subnets", "describe-subnets", "Subnets", lambda s: f"{s['SubnetId']}  ({s.get('CidrBlock', '')})"),
    ("Security groups", "describe-security-groups", "SecurityGroups", lambda g: f"{g['GroupId']}  ({g.get('GroupName', '')})"),
    ("Internet gateways", "describe-internet-gateways", "InternetGateways", lambda g: g["InternetGatewayId"]),
    ("Route tables", "describe-route-tables", "RouteTables", lambda r: r["RouteTableId"]),
    ("Key pairs", "describe-key-pairs", "KeyPairs", lambda k: k["KeyName"]),
]


def find_tagged(region, tag_value, command, list_name, describe):
    data = aws(["ec2", command, "--filters", f"Name=tag:{TAG_KEY},Values={tag_value}"], region)
    return [describe(item) for item in data.get(list_name, [])]


# The part of an ARN after the account number, for example "instance/i-0abc...".
def resource_of(arn):
    parts = arn.split(":", 5)
    return parts[5] if len(parts) == 6 else arn


# Show "service  type/id" instead of the whole ARN.
def describe_arn(arn):
    parts = arn.split(":", 5)
    return f"{parts[2]}  {parts[5]}" if len(parts) == 6 else hide_account_numbers(arn)


# AWS keeps listing a terminated machine for about an hour, and its tag index can do the same. Only a machine that is still alive
# is a leftover, so ask EC2 which of the machines named by the tag index are alive and drop the others.
def drop_dead_machines(region, arns):
    ids = [resource_of(a).split("/", 1)[1] for a in arns if resource_of(a).startswith("instance/")]
    if not ids:
        return arns

    filters = [f"Name=instance-id,Values={','.join(ids)}", f"Name=instance-state-name,Values={LIVE_STATES}"]
    alive = {
        instance["InstanceId"]
        for reservation in aws(["ec2", "describe-instances", "--filters", *filters], region).get("Reservations", [])
        for instance in reservation.get("Instances", [])
    }
    return [a for a in arns if not resource_of(a).startswith("instance/") or resource_of(a).split("/", 1)[1] in alive]


# Everything with the tag, in any service. AWS's tag index can be a little behind a deletion, so it is asked again
# every few seconds for up to "wait" seconds, as long as it still names something.
def find_tagged_anywhere(region, tag_value, wait):
    deadline = time.time() + wait
    while True:
        data = aws(["resourcegroupstaggingapi", "get-resources", "--tag-filters", f"Key={TAG_KEY},Values={tag_value}"], region)
        named = [item["ResourceARN"] for item in data.get("ResourceTagMappingList", [])]
        arns = [describe_arn(a) for a in drop_dead_machines(region, named)]
        if not arns or time.time() >= deadline:
            return arns
        time.sleep(POLL_SECONDS)


def find_volumes(region):
    return [describe_volume(v) for v in aws(["ec2", "describe-volumes"], region).get("Volumes", [])]


def find_addresses(region):
    data = aws(["ec2", "describe-addresses"], region)
    return [f"{a.get('AllocationId', '-')}  {a.get('PublicIp', '')}" for a in data.get("Addresses", [])]


def find_nat_gateways(region):
    data = aws(["ec2", "describe-nat-gateways"], region)
    return [f"{n['NatGatewayId']}  ({n['State']})" for n in data.get("NatGateways", []) if n["State"] in ("pending", "available")]


def find_load_balancers(region):
    modern = aws(["elbv2", "describe-load-balancers"], region)
    classic = aws(["elb", "describe-load-balancers"], region)
    return [lb["LoadBalancerName"] for lb in modern.get("LoadBalancers", [])] + [
        f"{lb['LoadBalancerName']}  (classic)" for lb in classic.get("LoadBalancerDescriptions", [])
    ]


# The resources that cost money: (title, how to find them, whether the check may be skipped when AWS refuses it).
BILLABLE = [
    ("EC2 instances", find_instances, False),
    ("EBS volumes", find_volumes, False),
    ("Elastic IP addresses", find_addresses, False),
    ("NAT gateways", find_nat_gateways, False),
    ("Load balancers", find_load_balancers, True),
]


def show(title, found):
    print(f"  [{'FOUND' if found else 'none '}] {title}" + (f" ({len(found)})" if found else ""))
    for item in found:
        print(f"            {item}")
    return len(found)


# Run one check and print its line.
def look(title, finder, skipped, optional=False):
    try:
        found = finder()
    except ScanError as error:
        if not (optional and error.denied):
            raise
        print(f"  [skip ] {title} (permission denied{': ' + error.action if error.action else ''})")
        skipped.append(f"{title} ({error.action})" if error.action else title)
        return 0
    return show(title, found)


# The resources that cost money in another region.
def find_billable_elsewhere(region):
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
    print("CA2 cleanup scan")
    print(f"  time    : {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"  account : ...{identity['Account'][-4:]}")
    print(f"  region  : {region}")
    print(f"  tag     : {TAG_KEY}={tag_value}")
    leftovers, skipped = 0, []

    print(f"\nCreated by this project (tagged {TAG_KEY}={tag_value})")
    project_total = look("EC2 instances", partial(find_instances, region, tag_value), skipped)
    for title, command, list_name, describe in TAGGED_TYPES:
        project_total += look(title, partial(find_tagged, region, tag_value, command, list_name, describe), skipped)
    leftovers += project_total

    # If the checks above found nothing, AWS's tag index may still list things that were deleted a moment ago: give it time.
    print("\nAnything else with that tag, in any AWS service")
    wait_now = wait if project_total == 0 else 0
    leftovers += look("Tagged resources", partial(find_tagged_anywhere, region, tag_value, wait_now), skipped, optional=True)

    print(f"\nResources that cost money, tagged or not, in {region}")
    for title, finder, optional in BILLABLE:
        leftovers += look(title, partial(finder, region), skipped, optional)

    if all_regions:
        others = sorted(r["RegionName"] for r in aws(["ec2", "describe-regions"], region).get("Regions", []) if r["RegionName"] != region)
        print(f"\nResources that cost money, in the other {len(others)} enabled regions")
        for other in others:
            found, refused = find_billable_elsewhere(other)
            mark = "FOUND" if found else ("skip " if refused else "none ")
            print(f"  [{mark}] {other}" + (f"  (permission denied: {', '.join(refused)})" if refused else ""))
            for title, items in found:
                leftovers += len(items)
                for item in items:
                    print(f"            {title}: {item}")
            if refused:
                skipped.append(f"{other}: {', '.join(refused)}")

    print()
    where = "in any region" if all_regions else f"in {region}"
    if leftovers == 0:
        print(f"RESULT: CLEAN - nothing tagged {TAG_KEY}={tag_value} is left, and no billable resources are running {where}.")
    else:
        print("RESULT: LEFTOVERS FOUND - the resources listed above are still in AWS.")
        print('        While the cluster exists, that is expected. After "make cluster-down" it is not: run it again, then scan again.')

    if skipped:
        print(f"        Not checked, because this AWS user is not allowed to: {'; '.join(skipped)}.")

    return 0 if leftovers == 0 else 1


def main():
    parser = argparse.ArgumentParser(description="Prove that nothing from this project is left in AWS.")
    parser.add_argument("--all-regions", action="store_true", help="also look for billable resources in every other enabled region")
    parser.add_argument("--wait", type=int, default=60, help="seconds to let AWS's tag index catch up after a deletion (default 60)")
    arguments = parser.parse_args()

    try:
        settings = read_config()
        if not settings.get("region") or not settings.get("project_tag"):
            raise ScanError("infra/config.yml must contain region and project_tag")
        return run_scan(settings["region"], settings["project_tag"], arguments.wait, arguments.all_regions)
    except ScanError as error:
        print(f"\nRESULT: COULD NOT SCAN - {error}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
