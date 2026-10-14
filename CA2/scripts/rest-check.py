# Calls the REST API from this computer, through the node port of every cluster node, and prints each request and answer.
#
#   make rest-check                                  (finds the node addresses by asking Terraform)
#   python3 scripts/rest-check.py --hosts 1.2.3.4    (or give the addresses yourself)
#
# What a pass proves:
#   1. the firewall lets the REST port through from this computer,
#   2. every node answers on the node port, also a node that runs no REST pod (the node forwards the request to a pod on another node),
#   3. the REST API can reach MongoDB (/ready), and
#   4. MongoDB holds every flow the producer sent (/stats), which means the whole pipeline worked: producer, Kafka, processor, MongoDB.

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


# Ask Terraform for the public addresses of the machines, so no address is ever typed into this project.
def node_addresses():
    terraform = os.environ.get("TERRAFORM", "terraform")
    out = subprocess.run([terraform, "-chdir=" + str(ROOT / "infra" / "terraform"), "output", "-json"], capture_output=True, text=True)

    if out.returncode != 0:
        sys.exit("Could not ask Terraform for the node addresses:\n" + out.stderr.strip())

    values = json.loads(out.stdout or "{}")

    if "server_public_ip" not in values or "agent_public_ips" not in values:
        sys.exit("Terraform knows no machines, so there is no cluster to call. Build one with:  make cluster-up")

    return [values["server_public_ip"]["value"]] + list(values["agent_public_ips"]["value"])


# The node port is written once, in infra/config.yml (the checker makes sure it equals the Service).
def node_port():
    found = re.search(r"^rest_node_port:\s*(\d+)", (ROOT / "infra" / "config.yml").read_text(), flags=re.M)
    return int(found.group(1)) if found else 30080


# One GET request. Returns (status code, body text), or (None, reason) when nobody answered.
def get(url, timeout=5):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as answer:
            return answer.status, answer.read().decode()
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode()
    except Exception as error:
        return None, error.__class__.__name__ + ": " + str(error)


def compact(text):
    try:
        return json.dumps(json.loads(text), sort_keys=True, separators=(",", ":"))
    except ValueError:
        return text.strip()[:120]


parser = argparse.ArgumentParser(description="Call the REST API through every node's node port.")
parser.add_argument("--hosts", help="comma-separated node addresses (default: ask Terraform)")
parser.add_argument("--port", type=int, help="node port (default: rest_node_port in infra/config.yml)")
parser.add_argument("--expect", type=int, default=5000, help="how many flows MongoDB should hold (default 5000, the size of the dataset)")
parser.add_argument("--wait", type=int, default=120, help="seconds to wait for the flows to arrive (default 120)")
args = parser.parse_args()

hosts = args.hosts.split(",") if args.hosts else node_addresses()
port = args.port or node_port()
names = ["server"] + ["agent-" + str(i) for i in range(1, len(hosts))] if not args.hosts else ["host-" + str(i) for i in range(1, len(hosts) + 1)]

print("REST API check from this computer: " + str(len(hosts)) + " nodes, node port " + str(port) + ", expecting " + str(args.expect) + " flows in MongoDB")

problems, documents_seen, silent = [], [], []

for name, host in zip(names, hosts):
    base = "http://" + host + ":" + str(port)
    print("node " + name + " (" + host + ")")

    status, body = get(base + "/health")

    if status is None:
        print("  GET /health -> no answer (" + body + ")")
        silent.append(name)
        continue

    print("  GET /health  -> " + str(status) + " " + compact(body))

    if status != 200:
        problems.append(name + ": /health answered " + str(status))

    status, body = get(base + "/ready")
    print("  GET /ready   -> " + str(status) + " " + compact(body))

    if status != 200:
        problems.append(name + ": /ready answered " + str(status) + " (the REST API cannot reach MongoDB)")

    # The processor may still be storing flows, so wait (up to --wait seconds) until the count reaches the goal.
    # If the request itself fails there is no point in waiting, so that case stops at once.
    deadline, documents = time.time() + args.wait, -1

    while True:
        status, body = get(base + "/stats")

        try:
            documents = json.loads(body)["documents"]
        except (ValueError, KeyError, TypeError):
            documents = -1

        if documents >= args.expect or status != 200 or time.time() >= deadline:
            break

        time.sleep(5)

    print("  GET /stats   -> " + str(status) + " " + compact(body))
    documents_seen.append(documents)

    if documents < args.expect:
        problems.append(name + ": MongoDB holds " + str(documents) + " flows, expected " + str(args.expect) + ". The processor may still be working: run  make logs")

    status, body = get(base + "/alerts")

    try:
        alerts = json.loads(body)
        first = alerts["alerts"][0].get("Label", "?") if alerts["alerts"] else "-"
        print("  GET /alerts  -> " + str(status) + " count=" + str(alerts["count"]) + ", first Label=" + str(first))
    except (ValueError, KeyError, TypeError, IndexError):
        print("  GET /alerts  -> " + str(status) + " " + compact(body))
        problems.append(name + ": /alerts did not answer with a list of alerts")

    status, body = get(base + "/events/no-such-id")
    print("  GET /events/no-such-id -> " + str(status) + " " + compact(body))

    if status != 404 or '"found":false' not in compact(body):
        problems.append(name + ": /events/no-such-id should answer 404 with found:false")

if silent:
    problems.append(", ".join(silent) + ": no answer. If your internet address changed (new Wi-Fi, a phone hotspot, a VPN), run  make cluster-up  to update the firewall, then try again.")

if problems:
    print("\nRESULT: FAIL")
    for p in problems:
        print("  - " + p)
    sys.exit(1)

print("\nRESULT: PASS - " + str(len(hosts)) + " of " + str(len(hosts)) + " nodes answered correctly and MongoDB holds " + str(min(documents_seen)) + " flows")
