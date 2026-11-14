# scripts/netpol-check.py against a stand-in kubectl: how the script behaves when the producer's pod is there, when it is not (the producer is a Job
# that exists only during a replay), and in the control experiment. The stand-in answers every connection with the table that is written in the
# script itself, so these tests are about what the script does with the answers (which columns it tries, what it prints, when it says FAIL),
# not about whether the table agrees with the policies; that is checked by reading policies/*.yaml and, on the real cluster, by the test itself.
#
# Why this file exists: the first run on a real cluster stopped with "no pod is ready behind the Service producer-metrics" in both modes. The
# check had been taken from CA3, where the producer ran all the time.

import ast
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

PIPELINE = Path(__file__).resolve().parent.parent
SCRIPT = PIPELINE / "scripts" / "netpol-check.py"

# The stand-in for kubectl. It keeps what was created in a JSON file and answers the questions that netpol-check.py asks.
FAKE_KUBECTL = r'''#!{python}
import ast, json, os, sys

args = sys.argv[1:]
state_path = os.environ["FAKE_STATE"]
state = json.load(open(state_path))
tree = ast.parse(open(os.environ["FAKE_SCRIPT"], encoding="utf-8").read())
tables = {{}}

for node in tree.body:
    if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) and node.targets[0].id in ("OPEN", "PIPELINE", "MONITORING"):
        tables[node.targets[0].id] = ast.literal_eval(node.value)

DNS = "10.43.0.10"


def save():
    json.dump(state, open(state_path, "w"))


def out(text="", code=0, err=""):
    sys.stdout.write(text)
    sys.stderr.write(err)
    sys.exit(code)


def flag(name):
    return args[args.index(name) + 1] if name in args else None


def ready_pod(name):
    return {{"metadata": {{"name": name, "labels": {{}}}}, "status": {{"phase": "Running", "conditions": [{{"type": "Ready", "status": "True"}}]}}}}


text = " ".join(args)

if "exec" in args:
    where = args[args.index("--") + 1:]
    probe = args[args.index("exec") + 1].replace("probe-", "", 1)

    if where[0] == "nc":
        host, port = where[-2], int(where[-1])
        title = None

        for title_, service, port_ in tables["PIPELINE"] + tables["MONITORING"]:
            name = DNS if service is None else (service if "." in service else service + ".nlp.svc.cluster.local")

            if name == host and port_ == port:
                title = title_

        if title is None:
            out(code=1, err="nc: bad address '" + host + "'")

        open_ = (not state["policies"]) or title in tables["OPEN"][probe]
        out(code=0 if open_ else 1, err="" if open_ else "nc: " + host + " (10.0.0.1:" + str(port) + "): Connection timed out")

    if where[0] == "wget":
        out("# HELP\nprocessor_ready 1\nprocessor_messages_total 5\nok\n")

    out()

if args[:2] == ["-n", "kube-system"] and "kube-dns" in args:
    out(DNS)

if "networkpolicy" in args and "get" in args:
    out("".join("networkpolicy.networking.k8s.io/p" + str(i) + "\n" for i in range(25)) if state["policies"] else "")

if "networkpolicy" in args and "delete" in args:
    state["policies"] = False
    save()
    out("networkpolicy.networking.k8s.io \"p0\" deleted\n" * 25)

if args[0] == "apply":
    state["policies"] = True
    save()
    out("networkpolicy.networking.k8s.io/p0 unchanged\n" * 25)

if "job" in args and "bootstrap" in args:
    out("1")

if "endpointslices" in args:
    service = flag("-l").split("=")[1]
    silent = os.environ.get("FAKE_SILENT", "").split(",")
    producer_missing = service == "producer-metrics" and os.environ.get("FAKE_PRODUCER") != "1"
    ready = not (service in silent or producer_missing)
    # A Service that has no pod has no endpoints. On a real cluster its slice is still there, and it says "endpoints": null (not an empty list).
    empty = [{{"endpoints": None}}] if os.environ.get("FAKE_SLICES") == "null" else []
    out(json.dumps({{"items": [{{"endpoints": [{{"conditions": {{"ready": True}}}}]}}] if ready else empty}}))

if "serviceaccount" in args:
    out("serviceaccount/default\n")

if args[0] == "create":
    item = json.loads(sys.stdin.read())

    if item["kind"] == "Namespace":
        state["namespaces"].append(item["metadata"]["name"])
    else:
        for pod in item["items"]:
            pod["status"] = {{"phase": "Running", "containerStatuses": [{{"state": {{"running": {{}}}}}}]}}
            state["probes"].append(pod)

    save()
    out("created\n")

if args[-3:-1] == ["delete", "namespace"] or ("delete" in args and "namespace" in args):
    state["namespaces"] = []
    save()
    out("namespace deleted\n")

if "delete" in args and "pods" in args:
    state["probes"] = []
    save()
    out("pods deleted\n")

if "namespace" in args and "get" in args and "-o" in args and args[args.index("-o") + 1] == "json":
    out(json.dumps({{"metadata": {{"name": args[-3], "uid": "uid-1"}}}}))

if "namespace" in args and "get" in args:
    out("namespace/nlp-intruder\n" if state["namespaces"] else "")

if "get" in args and "pods" in args and "-A" in args:
    out(json.dumps({{"items": state["probes"]}}))

if "get" in args and "pods" in args:
    selector = flag("-l") or ""

    if "processor" in selector or "restapi" in selector:
        out(json.dumps({{"items": [ready_pod("x-1")]}}))

    out(json.dumps({{"items": [ready_pod("kafka-0"), ready_pod("mongo-0")]}}))

out("", 1, "the stand-in kubectl does not know: " + text + "\n")
'''


def run_check(tmp_path, *extra, producer=False, silent="", slices="none"):
    fake = tmp_path / "kubectl"
    fake.write_text(FAKE_KUBECTL.format(python=sys.executable))
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"policies": True, "namespaces": [], "probes": []}))
    env = {**os.environ, "KUBECTL": str(fake), "NS": "nlp", "FAKE_STATE": str(state), "FAKE_SCRIPT": str(SCRIPT), "FAKE_SILENT": silent,
           "FAKE_PRODUCER": "1" if producer else "0", "FAKE_SLICES": slices, "PYTHONUNBUFFERED": "1"}
    done = subprocess.run([sys.executable, str(SCRIPT), "--patience", "1", *extra], capture_output=True, text=True, env=env, timeout=300)

    return done.returncode, done.stdout, json.loads(state.read_text())


def tables():
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    found = {}

    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) and node.targets[0].id in ("OPEN", "PIPELINE", "MONITORING", "CONTROL_TARGETS"):
            found[node.targets[0].id] = ast.literal_eval(node.value)

    return found


def test_with_a_producer_pod_all_169_connections_are_tried(tmp_path):
    code, out, state = run_check(tmp_path, producer=True)

    assert code == 0, out
    assert "Trying 169 connections" in out
    assert "Not tried" not in out
    assert "prod-metrics:8001" in out
    assert "The monitoring (91 connections)" in out
    assert "RESULT: PASS - all 169 connections behave as the policies say" in out
    assert "not tried" not in out.splitlines()[-1]
    assert "no probe pod and no namespace nlp-intruder left" in out
    assert state["probes"] == [] and state["namespaces"] == []


def test_without_a_producer_pod_that_column_is_left_out_and_the_report_says_so(tmp_path):
    code, out, state = run_check(tmp_path, producer=False)

    assert code == 0, out
    assert "Trying 156 connections" in out
    assert "Not tried: the producer's metrics port 8001 (prod-metrics)" in out
    assert "producer-metrics-ingress" in out
    assert "prod-metrics:8001" not in out
    assert out.count("The monitoring (78 connections)") == 1
    assert out.count("The pipeline (78 connections)") == 1
    assert out.splitlines()[-1].startswith("RESULT: PASS - all 156 connections behave as the policies say")
    assert out.splitlines()[-1].endswith("not tried: the producer's metrics port (no replay is running)")
    assert state["probes"] == [] and state["namespaces"] == []


# The run that was first saved as evidence stopped with "TypeError: 'NoneType' object is not iterable". On a real cluster the EndpointSlice of a Service
# that has no pod is there, and its "endpoints" is null, not a list. The stand-in used to answer with no slice at all, so that shape was never tried.
def test_a_slice_whose_endpoints_are_null_counts_as_no_pod(tmp_path):
    code, out, state = run_check(tmp_path, producer=False, slices="null")

    assert code == 0, out
    assert "Traceback" not in out
    assert "Not tried: the producer's metrics port 8001 (prod-metrics)" in out
    assert out.splitlines()[-1].startswith("RESULT: PASS - all 156 connections behave as the policies say")
    assert state["probes"] == [] and state["namespaces"] == []


def test_a_service_with_a_null_slice_and_no_pod_is_still_a_failure(tmp_path):
    code, out, state = run_check(tmp_path, producer=False, silent="restapi-metrics", slices="null")

    assert code == 1
    assert "Traceback" not in out
    assert "no pod is ready behind the Service restapi-metrics" in out
    assert "RESULT: FAIL" in out
    assert state["probes"] == [] and state["namespaces"] == []


def test_the_count_of_allowed_and_blocked_connections_follows_the_table(tmp_path):
    found = tables()
    allowed = sum(len([t for t in answers if t != "prod-metrics"]) for answers in found["OPEN"].values())
    code, out, state = run_check(tmp_path, producer=False)

    assert code == 0, out
    assert "(" + str(allowed) + " allowed, " + str(156 - allowed) + " blocked)" in out


def test_the_control_experiment_needs_no_producer(tmp_path):
    code, out, state = run_check(tmp_path, "--control", producer=False)

    assert code == 0, out
    assert "no pod is ready" not in out
    assert "RESULT: PASS - without the policies every connection worked" in out
    assert "Phase 1: the 25 policies are in place" in out
    assert "Phase 3: the policies are back" in out
    assert state["policies"] is True


def test_the_control_experiment_tries_only_its_own_targets(tmp_path):
    found = tables()
    code, out, state = run_check(tmp_path, "--control", producer=False)

    assert code == 0, out
    assert "prod-metrics" not in out and "rest-metrics" not in out

    for title in found["CONTROL_TARGETS"]:
        assert title + ":" in out


def test_a_service_that_has_no_pod_is_still_a_failure(tmp_path):
    code, out, state = run_check(tmp_path, producer=False, silent="restapi-metrics")

    assert code == 1
    assert "no pod is ready behind the Service restapi-metrics" in out
    assert "RESULT: FAIL" in out
    assert state["probes"] == [] and state["namespaces"] == []


def test_the_control_experiment_still_fails_when_one_of_its_own_services_has_no_pod(tmp_path):
    code, out, state = run_check(tmp_path, "--control", producer=True, silent="loki")

    assert code == 1
    assert "no pod is ready behind the Service loki" in out


def test_the_producers_column_is_the_only_optional_one():
    # A column that is optional must be one of the targets, and it must be the one that has a Job's pod behind it.
    source = SCRIPT.read_text(encoding="utf-8")
    found = tables()

    assert 'OPTIONAL = ("prod-metrics",)' in source
    assert ("prod-metrics", "producer-metrics", 8001) in found["MONITORING"]
    assert "prod-metrics" in found["OPEN"]["prometheus"]
    assert sum("prod-metrics" in answers for answers in found["OPEN"].values()) == 1


@pytest.mark.parametrize("probe", ["stranger", "producer", "loki"])
def test_nothing_but_prometheus_may_reach_the_producers_port(probe):
    assert "prod-metrics" not in tables()["OPEN"][probe]
