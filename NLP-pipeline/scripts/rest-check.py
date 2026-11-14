# Calls the REST API from this computer, through the node port of every cluster node, and prints each request and answer.
#
#   make rest-check                                  (finds the node addresses by asking Terraform)
#   python3 scripts/rest-check.py --hosts 1.2.3.4    (or give the addresses yourself)
#
# The REST API speaks HTTPS only and asks for a login. The CA certificate and the login are read from the Secrets pki-ca and restapi-login
# with kubectl (the password is never printed).
#
# What a pass proves:
#   1. the firewall lets the REST port through from this computer,
#   2. every node answers on the node port, also a node that runs no REST pod (the node forwards the request to a pod on another node),
#   3. the connection is encrypted and the certificate is the one of the stack's own CA (a client that does not trust that CA is refused),
#   4. the API asks for a login: no login and a wrong password are both answered with 401, and plain HTTP gets no answer at all,
#      while the two probe addresses /health and /ready (no data) stay open,
#   5. the REST API can reach MongoDB (/ready), and
#   6. the data endpoints answer with the right shapes (/stats, /runs, /summary, /alerts, /events/<id>). If a replay was done (make replay),
#      they hold its lines and windows; with --expect N the check also demands at least N stored log lines.

import argparse
import json
import sys
import time

import nlplib as lib


def compact(text):
    try:
        return json.dumps(json.loads(text), sort_keys=True, separators=(",", ":"))
    except ValueError:
        return text.strip()[:120]


parser = argparse.ArgumentParser(description="Call the REST API through every node's node port.")
parser.add_argument("--hosts", help="comma-separated node addresses (default: ask Terraform)")
parser.add_argument("--port", type=int, help="node port (default: rest_node_port in infra/config.yml)")
parser.add_argument("--expect", type=int, default=0, help="how many stored log lines MongoDB should hold at least (default 0: the check works before the first replay too)")
parser.add_argument("--wait", type=int, default=120, help="seconds to wait for --expect lines to arrive (default 120)")
args = parser.parse_args()

try:
    hosts = args.hosts.split(",") if args.hosts else lib.node_addresses()
    port = args.port or lib.node_port()
    names = ["server"] + ["agent-" + str(i) for i in range(1, len(hosts))] if not args.hosts else ["host-" + str(i) for i in range(1, len(hosts) + 1)]
    user, _ = lib.rest_login()
except lib.Fail as problem:
    print("RESULT: FAIL - " + str(problem))
    sys.exit(1)

print("REST API check from this computer: " + str(len(hosts)) + " nodes, node port " + str(port) + ", HTTPS with the login of the Secret restapi-login (user " + user + "), expecting at least " + str(args.expect) + " stored log lines in MongoDB")

problems, documents_seen, silent = [], [], []

for name, host in zip(names, hosts):
    print("node " + name + " (" + host + ")")

    # The two probe addresses need no login.
    status, body = lib.rest_get(host, port, "/health", login=False)

    if status is None:
        print("  GET /health  (no login)      -> no answer (" + body + ")")
        silent.append(name)
        continue

    print("  GET /health  (no login)      -> " + str(status) + " " + compact(body))

    if status != 200:
        problems.append(name + ": /health answered " + str(status))

    status, body = lib.rest_get(host, port, "/ready", login=False)
    print("  GET /ready   (no login)      -> " + str(status) + " " + compact(body))

    if status != 200:
        problems.append(name + ": /ready answered " + str(status) + " (the REST API cannot reach MongoDB)")

    # Everything else needs the login.
    status, body = lib.rest_get(host, port, "/stats", login=False)
    print("  GET /stats   (no login)      -> " + str(status) + " " + compact(body))

    if status != 401:
        problems.append(name + ": /stats without a login should answer 401, it answered " + str(status))

    status, body = lib.rest_get(host, port, "/stats", user=user, password="not-the-password")
    print("  GET /stats   (wrong password)-> " + str(status) + " " + compact(body))

    if status != 401:
        problems.append(name + ": /stats with a wrong password should answer 401, it answered " + str(status))

    # The connection must be encrypted, and only a client that trusts the stack's CA may proceed.
    status, body = lib.rest_get_plain(host, port, "/health")
    print("  GET /health  (plain http)    -> " + ("no answer, as it must be (" + body[:70] + ")" if status is None else str(status) + " " + compact(body)))

    if status is not None:
        problems.append(name + ": plain HTTP got an answer (" + str(status) + "); the REST API must speak HTTPS only")

    status, body = lib.rest_get(host, port, "/health", login=False, ca=False)
    print("  GET /health  (CA not trusted)-> " + ("refused by the client, as it must be (" + body[:70] + ")" if status is None else str(status) + " " + compact(body)))

    if status is not None:
        problems.append(name + ": a client that does not trust the stack's CA was able to connect")

    # The processor may still be storing lines, so wait (up to --wait seconds) until the count reaches the goal.
    # If the request itself fails there is no point in waiting, so that case stops at once.
    deadline, documents = time.time() + args.wait, -1

    while True:
        status, body = lib.rest_get(host, port, "/stats")

        try:
            documents = json.loads(body)["documents"]
        except (ValueError, KeyError, TypeError):
            documents = -1

        if documents >= args.expect or status != 200 or time.time() >= deadline:
            break

        time.sleep(5)

    print("  GET /stats   (with login)    -> " + str(status) + " " + compact(body))
    documents_seen.append(documents)

    if documents < args.expect:
        problems.append(name + ": MongoDB holds " + str(documents) + " log lines, expected at least " + str(args.expect) + ". The processor may still be working: run  make logs")

    status, body = lib.rest_get(host, port, "/alerts")

    try:
        alerts = json.loads(body)
        first = alerts["alerts"][0].get("truth", "?") if alerts["alerts"] else "-"
        print("  GET /alerts  (with login)    -> " + str(status) + " count=" + str(alerts["count"]) + ", first alert tag=" + str(first))
    except (ValueError, KeyError, TypeError, IndexError):
        print("  GET /alerts  (with login)    -> " + str(status) + " " + compact(body))
        problems.append(name + ": /alerts did not answer with a list of alerts")

    # The replays: a list (empty before the first replay). The summary is the answer for the newest one, or 404 if there is none yet.
    status, body = lib.rest_get(host, port, "/runs")

    try:
        listed = json.loads(body)["runs"]
        print("  GET /runs    (with login)    -> " + str(status) + " " + str(len(listed)) + " replay(s)" + (", newest: " + str(listed[0].get("run")) + " (" + str(listed[0].get("status")) + ")" if listed else ""))
    except (ValueError, KeyError, TypeError, IndexError):
        print("  GET /runs    (with login)    -> " + str(status) + " " + compact(body))
        problems.append(name + ": /runs did not answer with a list of replays")
        listed = None

    if listed is not None:
        status, body = lib.rest_get(host, port, "/summary")

        try:
            summary = json.loads(body)
            short = ("windows=" + str(summary["windows"]) + ", detectors=" + ",".join(sorted(summary["detectors"]))) if status == 200 else compact(body)
        except (ValueError, KeyError, TypeError):
            short, summary = compact(body), None

        print("  GET /summary (with login)    -> " + str(status) + " " + short)

        if listed and (status != 200 or summary is None):
            problems.append(name + ": /summary should answer 200 when a replay exists, it answered " + str(status))
        elif not listed and status != 404:
            problems.append(name + ": /summary should answer 404 before the first replay, it answered " + str(status))

    status, body = lib.rest_get(host, port, "/events/no-such-id")
    print("  GET /events/no-such-id       -> " + str(status) + " " + compact(body))

    if status != 404 or '"found":false' not in compact(body):
        problems.append(name + ": /events/no-such-id should answer 404 with found:false")

if silent:
    problems.append(", ".join(silent) + ": no answer. If your internet address changed (new Wi-Fi, a phone hotspot, a VPN), run  make cluster-up  to update the firewall, then try again.")

if problems:
    print("\nRESULT: FAIL")
    for p in problems:
        print("  - " + p)
    sys.exit(1)

print("\nRESULT: PASS - " + str(len(hosts)) + " of " + str(len(hosts)) + " nodes answered correctly (HTTPS only, login required) and MongoDB holds " + str(min(documents_seen)) + " stored log lines")
