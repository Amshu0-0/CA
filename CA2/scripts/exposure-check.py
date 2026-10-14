# Tries to open TCP connections to every cluster node, from this computer, and reports which ports answer.
#
#   make exposure-check                               (finds the node addresses by asking Terraform)
#   python3 scripts/exposure-check.py --hosts 1.2.3.4
#
# The security group (the AWS firewall) lets exactly three ports through, and only from this computer's address:
# SSH 22, the Kubernetes API 6443 and the REST API node port 30080. This script checks it from the outside.
# Each port ends up in one of three states:
#   open       the connection was accepted
#   refused    the connection reached the machine, but no program listens on that port
#   no answer  nothing came back (the firewall dropped the connection, or the machine ignored it)
# What is judged:
#   - an allowed port must be "open" on every machine that runs the program behind it: SSH and the REST node port on all
#     machines, the Kubernetes API (6443) on the server only. The workers run no API, so what they answer on 6443 is not
#     judged: the firewall rule covers them, but an empty port proves nothing about the firewall (the real workers stay silent).
#   - every other port must show "no answer" on every machine.
# It only touches your own machines, and it makes 11 connection attempts per node.

import argparse
import json
import os
import re
import socket
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# The ports to try, and what would listen on them inside the cluster. Most of them are real Kubernetes or pipeline ports.
# Ports 80 and 443 are left out on purpose: some networks (mobile data, campus, office) answer for them on behalf of any address.
PORTS = {
    22: "SSH",
    2379: "etcd",
    6443: "Kubernetes API",
    8000: "processor metrics",
    8080: "REST API (container port)",
    9092: "Kafka",
    10250: "kubelet",
    27017: "MongoDB",
    30080: "REST API node port",
    30081: "another node port",
    32767: "last node port",
}

# Only the server runs the Kubernetes API. The first address (the server) is judged on these ports, the others are not.
SERVER_ONLY = {6443}


def node_addresses():
    terraform = os.environ.get("TERRAFORM", "terraform")
    out = subprocess.run([terraform, "-chdir=" + str(ROOT / "infra" / "terraform"), "output", "-json"], capture_output=True, text=True)

    if out.returncode != 0:
        sys.exit("Could not ask Terraform for the node addresses:\n" + out.stderr.strip())

    values = json.loads(out.stdout or "{}")

    if "server_public_ip" not in values or "agent_public_ips" not in values:
        sys.exit("Terraform knows no machines, so there is no cluster to check. Build one with:  make cluster-up")

    return [values["server_public_ip"]["value"]] + list(values["agent_public_ips"]["value"])


def node_port():
    found = re.search(r"^rest_node_port:\s*(\d+)", (ROOT / "infra" / "config.yml").read_text(), flags=re.M)
    return int(found.group(1)) if found else 30080


def probe(host, port, timeout):
    try:
        socket.create_connection((host, port), timeout=timeout).close()
        return "open"
    except ConnectionRefusedError:
        return "refused"
    except OSError:
        return "no answer"


# A port is judged on a node when a program should listen there. The server is the first node.
def judged(index, port):
    return index == 0 or port not in SERVER_ONLY


parser = argparse.ArgumentParser(description="Show which ports of the cluster nodes answer from this computer.")
parser.add_argument("--hosts", help="comma-separated node addresses (default: ask Terraform)")
parser.add_argument("--ports", help="comma-separated ports to try (default: 11 well-known ones)")
parser.add_argument("--allowed", help="comma-separated ports the firewall should let through (default: 22, 6443 and the REST node port)")
parser.add_argument("--timeout", type=float, default=3.0, help="seconds to wait for each connection (default 3)")
args = parser.parse_args()

hosts = args.hosts.split(",") if args.hosts else node_addresses()
rest_port = node_port()
ports = [int(p) for p in args.ports.split(",")] if args.ports else sorted(PORTS)
allowed = {int(p) for p in args.allowed.split(",")} if args.allowed else {22, 6443, rest_port}
names = ["server"] + ["agent-" + str(i) for i in range(1, len(hosts))] if not args.hosts else ["host-" + str(i) for i in range(1, len(hosts) + 1)]

print("Port check from this computer: " + str(len(hosts)) + " nodes x " + str(len(ports)) + " ports (connection timeout " + str(args.timeout).rstrip("0").rstrip(".") + " s)")

jobs = [(h, p) for h in hosts for p in ports]

with ThreadPoolExecutor(max_workers=32) as pool:
    results = dict(zip(jobs, pool.map(lambda job: probe(job[0], job[1], args.timeout), jobs)))

print("\n  port  what" + " " * 34 + "".join(n.ljust(11) for n in names))

for p in ports:
    print("  " + str(p).ljust(6) + PORTS.get(p, "").ljust(38) + "".join((results[(h, p)] + ("" if judged(i, p) else "*")).ljust(11) for i, h in enumerate(hosts)))

for p in ports:
    if p in allowed and p in SERVER_ONLY and len(hosts) > 1:
        print("\n  * not judged: only the server runs the " + PORTS.get(p, "program behind port " + str(p)) + ", so the other machines have nothing to show on port " + str(p))

problems = []

for i, (h, n) in enumerate(zip(hosts, names)):
    for p in ports:
        state = results[(h, p)]

        if p in allowed and judged(i, p):
            if state == "no answer":
                problems.append(n + ": no answer on port " + str(p) + ", which the firewall should let through. If your internet address changed (new Wi-Fi, a hotspot, a VPN), run  make cluster-up")
            elif state == "refused":
                problems.append(n + ": port " + str(p) + " refuses the connection: the firewall lets it through, but nothing listens there. Run  make diagnose")
        elif p not in allowed and state != "no answer":
            problems.append(n + ": port " + str(p) + " is '" + state + "', but the firewall should drop it")

if problems:
    print("\nRESULT: FAIL")
    for line in problems:
        print("  - " + line)
    sys.exit(1)

passed = ", ".join(str(p) + " (" + PORTS.get(p, "?") + (", server only" if p in SERVER_ONLY and len(hosts) > 1 else "") + ")" for p in sorted(allowed) if p in ports)
print("\nRESULT: PASS - on all " + str(len(hosts)) + " nodes only " + passed + " answer; the other " + str(len([p for p in ports if p not in allowed])) + " ports are dropped")
