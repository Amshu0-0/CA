# Tests the NetworkPolicies with real connections.
#
#   make netpol-check      who can reach whom, tried from six short-lived "probe" pods (changes nothing in the stack)
#   make netpol-control    the same, once with the policies deleted and once with them put back (proves they are what blocks)
#
# A NetworkPolicy picks pods by their labels. A probe pod wears the label of one role (producer, processor, REST API,
# observer), so the policies treat it exactly like that role. A "stranger" wears no role, and an "intruder" lives in
# another namespace. Each probe pod tries to open a TCP connection ("nc -z") to Kafka, MongoDB, the processor's metrics
# port, the REST API and the DNS server, and the result is compared with what policies/networkpolicies.yaml says.
#
# The rules reach a new pod a few seconds after it starts, so an answer that is not the expected one is tried again for up
# to --patience seconds before it counts, and the report says when that was needed.
# The probe pods and the namespace ca2-intruder are removed at the end, also when the test fails.

import argparse
import json
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor

import ca2lib as lib

INTRUDER_NS = "ca2-intruder"
TEST = "app.kubernetes.io/part-of=ca2-test"
NAME, COMPONENT = "app.kubernetes.io/name", "app.kubernetes.io/component"

# The probe pods: (short name, namespace, labels, what it stands for).
PROBES = [
    ("stranger", lib.NS, {NAME: "stranger"}, "no role, in ca2"),
    ("intruder", INTRUDER_NS, {NAME: "intruder"}, "another namespace"),
    ("producer", lib.NS, {NAME: "producer"}, "wears the producer label"),
    ("processor", lib.NS, {NAME: "processor"}, "wears the processor label"),
    ("restapi", lib.NS, {NAME: "restapi"}, "wears the REST API label"),
    ("observer", lib.NS, {NAME: "observer", COMPONENT: "observer"}, "the monitoring role"),
]

# What each probe tries to reach: (column title, Service name, port). The DNS server is found by its address.
TARGETS = [("kafka", "kafka", 9092), ("mongo", "mongo", 27017), ("processor", "processor", 8000), ("restapi", "restapi", 8080), ("DNS", None, 53)]

# What the policies say should happen (True = the connection works). A connection needs two permissions: the sender needs an
# egress rule and the receiver needs an ingress rule. Everything is denied by default, except DNS (allow-dns-egress).
EXPECT = {
    "stranger": {"kafka": False, "mongo": False, "processor": False, "restapi": False, "DNS": True},
    "intruder": {"kafka": False, "mongo": False, "processor": False, "restapi": True, "DNS": True},
    "producer": {"kafka": True, "mongo": False, "processor": False, "restapi": False, "DNS": True},
    "processor": {"kafka": True, "mongo": True, "processor": False, "restapi": False, "DNS": True},
    "restapi": {"kafka": False, "mongo": True, "processor": False, "restapi": False, "DNS": True},
    "observer": {"kafka": False, "mongo": False, "processor": True, "restapi": True, "DNS": True},
}

MEANING = [
    "stranger   reaches nothing but DNS: default-deny-all blocks the rest, and allow-dns-egress opens only the DNS server",
    "intruder   (another namespace) reaches only the REST API: restapi-ingress is the one policy that accepts any sender",
    "producer   reaches only Kafka: producer-egress lets it out, kafka-ingress lets it in",
    "processor  reaches Kafka and MongoDB: processor-egress, kafka-ingress and mongo-ingress",
    "restapi    reaches only MongoDB: restapi-egress and mongo-ingress",
    "observer   reaches the processor's metrics port and the REST API: observer-egress, processor-ingress and restapi-ingress",
]

failed = []


def pod_manifest(short, namespace, labels):
    return {
        "apiVersion": "v1", "kind": "Pod",
        "metadata": {"name": "probe-" + short, "namespace": namespace, "labels": {**labels, "app.kubernetes.io/part-of": "ca2-test"}},
        "spec": {
            "restartPolicy": "Never", "automountServiceAccountToken": False, "enableServiceLinks": False,
            "terminationGracePeriodSeconds": 1, "activeDeadlineSeconds": 1800,
            "securityContext": {"runAsNonRoot": True, "runAsUser": 1000, "runAsGroup": 1000, "seccompProfile": {"type": "RuntimeDefault"}},
            "containers": [{
                "name": "probe", "image": "busybox:1.36", "command": ["sleep", "1800"],
                "resources": {"requests": {"cpu": "10m", "memory": "16Mi"}, "limits": {"cpu": "100m", "memory": "32Mi"}},
                "securityContext": {"allowPrivilegeEscalation": False, "capabilities": {"drop": ["ALL"]}},
            }],
        },
    }


# Remove every probe pod and the other namespace. Safe to call when nothing is there.
def cleanup():
    lib.kubectl("-n", lib.NS, "delete", "pods", "-l", TEST, "--now", "--wait=true", "--timeout=60s", timeout=90)
    lib.kubectl("delete", "namespace", INTRUDER_NS, "--ignore-not-found", "--wait=true", "--timeout=120s", timeout=150)


def leftovers():
    pods = lib.kube_json("get", "pods", "-A", "-l", TEST)["items"]
    code, out, err = lib.kubectl("get", "namespace", INTRUDER_NS, "--ignore-not-found", "-o", "name")

    return [p["metadata"]["namespace"] + "/" + p["metadata"]["name"] for p in pods] + ([out.strip()] if out.strip() else [])


# One line about a probe pod that is not ready: its phase, why a container waits (for example ImagePullBackOff), or why it has no node.
def why_not_ready(pod):
    status = pod.get("status", {})
    waiting = [c["state"]["waiting"].get("reason", "waiting") for c in status.get("containerStatuses", []) if "waiting" in c.get("state", {})]
    unplaced = [c.get("message", "") for c in status.get("conditions", []) if c.get("type") == "PodScheduled" and c.get("status") == "False"]

    return pod["metadata"]["name"] + ": " + status.get("phase", "?") + (", " + ", ".join(waiting) if waiting else "") + (", no node yet: " + unplaced[0][:140] if unplaced else "")


def start_probes(wanted, wait=180):
    if "intruder" in wanted:
        namespace = {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": INTRUDER_NS, "labels": {"app.kubernetes.io/part-of": "ca2-test"}}}
        code, out, err = lib.kubectl("create", "-f", "-", input=json.dumps(namespace))

        if code != 0:
            raise lib.Fail("could not create the namespace " + INTRUDER_NS + ":\n" + err.strip())

        # A new namespace gets its "default" ServiceAccount a moment later, and a pod can be created only after that.
        if not lib.wait_for(lambda: lib.kubectl("-n", INTRUDER_NS, "get", "serviceaccount", "default", "-o", "name")[0] == 0, 30, interval=1)[0]:
            raise lib.Fail("the namespace " + INTRUDER_NS + " did not get its default ServiceAccount within 30 s")

    items = [pod_manifest(*p[:3]) for p in PROBES if p[0] in wanted]
    code, out, err = lib.kubectl("create", "-f", "-", input=json.dumps({"apiVersion": "v1", "kind": "List", "items": items}))

    if code != 0:
        raise lib.Fail("could not create the probe pods:\n" + err.strip())

    def all_ready():
        pods = lib.kube_json("get", "pods", "-A", "-l", TEST)["items"]
        return pods if len(pods) == len(wanted) and all(lib.pod_is_ready(p) for p in pods) else None

    if not lib.wait_for(all_ready, wait, interval=3)[0]:
        states = [why_not_ready(p) for p in lib.kube_json("get", "pods", "-A", "-l", TEST)["items"]]
        raise lib.Fail("the probe pods did not start within " + str(wait) + " s (an image that cannot be downloaded is the usual reason):\n  " + "\n  ".join(states))


# Try to open one TCP connection from a probe pod. Returns (it worked, the last line of what nc said).
def connect(short, namespace, host, port):
    try:
        code, out, err = lib.kubectl("-n", namespace, "exec", "probe-" + short, "--", "nc", "-z", "-w", "3", host, str(port), timeout=25)
    except lib.Fail:
        return False, "no answer within 25 s"

    said = (err.strip() or out.strip()).splitlines()

    return code == 0, (said[-1] if said else "")


# Try again every 3 seconds until the answer is the expected one, or the patience is used up.
def settled(short, namespace, host, port, expected, patience):
    start, first = time.monotonic(), None

    while True:
        worked, said = connect(short, namespace, host, port)
        first = worked if first is None else first

        if worked == expected or time.monotonic() - start >= patience:
            return {"worked": worked, "first": first, "said": said, "seconds": time.monotonic() - start}

        time.sleep(3)


# All the connections of one probe pod, one after the other: {target title: result}.
# The patience is a budget for the whole pod, so a cluster that enforces nothing fails after about one patience, not after one per probe.
def probe_all(probe, hosts, expected, patience):
    deadline, results = time.monotonic() + patience, {}

    for t in expected:
        results[t] = settled(probe[0], probe[1], hosts[t][0], hosts[t][1], expected[t], max(0.0, deadline - time.monotonic()))

    return results


# Run the probes of several pods at the same time. Returns [(probe, {target title: result})].
def run_probes(probes, hosts, expected, patience):
    with ThreadPoolExecutor(max_workers=len(probes)) as pool:
        jobs = [(p, pool.submit(probe_all, p, hosts, expected[p[0]], patience)) for p in probes]

        return [(p, j.result()) for p, j in jobs]


def word(worked):
    return "open" if worked else "blocked"


def show(done, expected, titles, hosts):
    print("  from".ljust(46) + "".join((t + ":" + str(hosts[t][1])).ljust(16) for t in titles))

    for p, results in done:
        print("  " + (p[0] + " (" + p[3] + ")").ljust(44) + "".join((word(results[t]["worked"]) + ("" if results[t]["worked"] == expected[p[0]][t] else "!")).ljust(16) for t in titles))


# Record every answer that is not the expected one, and say which answers needed a second try.
def judge(done, expected, label="", why="the rules reach a new pod a few seconds after it starts"):
    for p, results in done:
        for t, r in results.items():
            if r["worked"] != expected[p[0]][t]:
                failed.append(label + p[0] + " -> " + t + ": " + word(r["worked"]) + ", expected " + word(expected[p[0]][t]) + (" (" + r["said"] + ")" if r["said"] else ""))

    late = [p[0] + " -> " + t + " (" + word(r["first"]) + " at first, " + word(r["worked"]) + " after " + str(round(r["seconds"])) + " s)" for p, results in done for t, r in results.items() if r["first"] != r["worked"] and r["worked"] == expected[p[0]][t]]

    if late:
        print("\n  Answers that needed a second try (" + why + "):")
        for line in late:
            print("    " + line)


def run_table(hosts, patience):
    print("\nStarting " + str(len(PROBES)) + " probe pods (busybox; one of them in the namespace " + INTRUDER_NS + ")...")
    start_probes([p[0] for p in PROBES])
    print("Trying " + str(len(PROBES) * len(TARGETS)) + " connections (a blocked one can take up to 3 seconds to give up)...\n")
    done = run_probes(PROBES, hosts, EXPECT, patience)
    show(done, EXPECT, [t[0] for t in TARGETS], hosts)
    print("\n  open = the connection was accepted; blocked = it did not get through; ! = not what the policies say")
    judge(done, EXPECT)
    print("\nWhat that shows:")

    for line in MEANING:
        print("  " + line)

    # What the observer can read: the processor's metrics, through the one port that its policies open.
    code, out, err = lib.kubectl("-n", lib.NS, "exec", "probe-observer", "--", "wget", "-q", "-T", "3", "-O", "-", "http://" + hosts["processor"][0] + ":" + str(hosts["processor"][1]) + "/metrics", timeout=30)
    shown = [l for l in out.splitlines() if l.startswith(("processor_messages_total", "processor_alerts_total", "processor_ready"))]
    print("\nThe observer pod reads the processor's metrics port: " + ("; ".join(shown) if shown else "no answer (" + (err.strip() or "empty")[:120] + ")"))


def run_control(hosts, policies):
    names = ("stranger", "intruder")
    probes = [p for p in PROBES if p[0] in names]
    keep = ["kafka", "mongo", "processor", "restapi"]
    with_policies = {n: {t: EXPECT[n][t] for t in keep} for n in names}
    without_policies = {n: {t: True for t in keep} for n in names}
    where = {t: hosts[t] for t in keep}

    print("\nStarting 2 probe pods (a stranger in " + lib.NS + " and an intruder in " + INTRUDER_NS + ")...")
    start_probes(names)

    # One phase: try every connection until it gives the expected answer, show the table, and say how long it took.
    def phase(title, expected, patience, outcome=None):
        print("\n" + title)
        before = len(failed)
        done = run_probes(probes, where, expected, patience)
        show(done, expected, keep, hosts)
        judge(done, expected, title.split(":")[0] + ", ", "the change reaches the pods a few seconds after the policies change")

        if outcome and len(failed) == before:
            print("\n  " + outcome + " after at most " + str(round(max(r["seconds"] for p, results in done for r in results.values()))) + " s")

    try:
        phase("Phase 1: the " + str(len(policies)) + " policies are in place", with_policies, 30)
        code, out, err = lib.kubectl("-n", lib.NS, "delete", "networkpolicy", "--all")
        print("\n  kubectl delete networkpolicy --all     (" + str(len([l for l in out.splitlines() if "deleted" in l])) + " deleted)")
        phase("Phase 2: no policies", without_policies, 90, "every connection was accepted")
    finally:
        code, out, err = lib.kubectl("apply", "-f", str(lib.ROOT / "policies" / "networkpolicies.yaml"))
        print("\n  kubectl apply -f policies/networkpolicies.yaml     (" + str(len([l for l in out.splitlines() if "created" in l])) + " created, " + str(len([l for l in out.splitlines() if "unchanged" in l])) + " unchanged)")

        if code != 0:
            failed.append("the policies could not be applied again (" + err.strip() + "). Run NOW:  make up")

    phase("Phase 3: the policies are back", with_policies, 90, "the original answers were back")


parser = argparse.ArgumentParser(description="Test the NetworkPolicies with probe pods.")
parser.add_argument("--control", action="store_true", help="delete all policies, test, apply them again, test (changes the cluster for about a minute)")
parser.add_argument("--patience", type=int, default=30, help="seconds to keep trying when an answer is not the expected one (default 30)")
args = parser.parse_args()

try:
    code, dns, err = lib.kubectl("-n", "kube-system", "get", "svc", "kube-dns", "-o", "jsonpath={.spec.clusterIP}")

    if code != 0 or not dns.strip():
        raise lib.Fail("could not find the DNS Service kube-dns in kube-system: " + err.strip())

    hosts = {t: ((s + "." + lib.NS + ".svc.cluster.local") if s else dns.strip(), port) for t, s, port in TARGETS}
    policies = [n for n in lib.kubectl("-n", lib.NS, "get", "networkpolicy", "-o", "name")[1].split() if n]
    print("NetworkPolicy test: " + str(len(policies)) + " policies in namespace " + lib.NS + (", control experiment" if args.control else ""))

    if not policies:
        raise lib.Fail("there are no NetworkPolicies in " + lib.NS + ", so there is nothing to test. Run:  make up")

    problems = lib.stack_problems()

    if problems:
        raise lib.Fail("the stack is not ready: " + ", ".join(problems) + ". Run:  make status")

    cleanup()
    run_control(hosts, policies) if args.control else run_table(hosts, args.patience)
except lib.Fail as problem:
    failed.append(str(problem))
except KeyboardInterrupt:
    print("\nControl-C: stopping and cleaning up...")
    failed.append("you stopped the test with Control-C")
except Exception:
    traceback.print_exc()
    failed.append("the test itself broke (see the error above)")
finally:
    try:
        cleanup()
        left = leftovers()
        print("\nClean-up: " + ("no probe pod and no namespace " + INTRUDER_NS + " left" if not left else "STILL THERE: " + ", ".join(left) + "   (make down removes them)"))
    except Exception as problem:
        print("\nClean-up could not be checked: " + str(problem))

if failed:
    print("\nRESULT: FAIL")
    for line in failed:
        print("  - " + line)
    sys.exit(1)

if args.control:
    print("\nRESULT: PASS - without the policies every connection worked, with them only the allowed ones do: the policies are what blocks")
else:
    allowed = sum(1 for answers in EXPECT.values() for expected in answers.values() if expected)
    total = len(PROBES) * len(TARGETS)
    print("\nRESULT: PASS - all " + str(total) + " connections behave as the policies say (" + str(allowed) + " allowed, " + str(total - allowed) + " blocked)")
