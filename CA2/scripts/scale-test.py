# Scaling test: what happens when the autoscaler adds producer pods, measured before and after.
#
#   make scale-test                                          the whole test, about 10 minutes
#   make scale-test SCALE_ARGS="--before 30 --after 30"      shorter measuring windows
#   make scale-report                                        draw the table and the chart again from the saved files
#
# Steps (the load comes from producer/scaling/, which "make up" never starts):
#   1. Check that the stack runs, that the REST API answers from this computer, and that CPU numbers are available.
#   2. Start ONE producer pod that replays the dataset in a loop (deployment-load.yaml) and measure it for 60 seconds. This is "before".
#   3. Create the HorizontalPodAutoscaler (hpa.yaml) and watch it add producer pods, up to its maximum. Kubernetes does this by itself.
#   4. Measure the larger set of producer pods for 60 seconds. This is "after".
#   5. Remove the load and the autoscaler (also when the test stops early or you press Ctrl-C) and wait until the processors have
#      stored the backlog.
#
# While it runs, a second thread takes a sample every 5 seconds and appends it to <prefix>-samples.csv:
#   pods and autoscaler decisions   kubectl get pods,hpa
#   messages per second per pod     the producers print one line every 5 seconds (kubectl logs)
#   records stored in MongoDB       GET /stats on the REST API, called from this computer
#   backlog (consumer lag)          kafka-consumer-groups inside the Kafka pod, every 20 seconds
#   CPU of the nodes                kubectl top nodes
# In each measuring window one more tagged event (the same one as the smoke test) is sent, and the test times how long it takes to
# get from Kafka to the REST API. That is the end-to-end delay the pipeline shows under that load.
#
# The processors have an autoscaler too (it is part of the stack). Even one producer keeps them busy, so they scale up in the first minute.
# The test waits until the number of pods has stopped changing before it starts a measuring window.
#
# The producers send as fast as their CPU limit allows, and a message is about 2.3 KB, so the test protects the disks of the cluster:
# it stops the load when the producers have sent --max-messages messages in total (1,500,000 are about 3.5 GB in Kafka), when a Kafka or
# MongoDB pod restarts, or after --max-minutes. A normal run sends roughly 500,000 to 900,000.

import argparse
import csv
import json
import re
import sys
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

import ca2lib as lib
import scalereport as report

LOAD_DIR = lib.ROOT / "producer" / "scaling"
LOAD_SELECTOR = "app.kubernetes.io/name=producer,app.kubernetes.io/component=load-generator"
# The line a producer prints every 5 seconds in replay-forever mode, for example:
# [pod/producer-load-6d9f8b-x2k4j/producer] sent 21480 rows total, 804 msg/s
RATE_LINE = re.compile(r"^\[pod/(\S+?)/producer\] sent (\d+) rows total, (\d+) msg/s\s*$")
STORES = ("kafka-0", "mongo-0")

parser = argparse.ArgumentParser(description="Measure the pipeline before and after the autoscaler adds producer pods.")
parser.add_argument("--before", type=int, default=60, help="seconds to measure with one producer pod (default 60)")
parser.add_argument("--after", type=int, default=60, help="seconds to measure with the autoscaled producers (default 60)")
parser.add_argument("--settle", type=int, default=20, help="seconds the number of pods must stay unchanged before a window starts (default 20)")
parser.add_argument("--settle-limit", type=int, default=240, help="the longest time to wait for that (default 240)")
parser.add_argument("--interval", type=float, default=5.0, help="seconds between samples (default 5)")
parser.add_argument("--lag-every", type=float, default=20.0, help="seconds between asking Kafka for the backlog (default 20; it costs the Kafka pod a little CPU)")
parser.add_argument("--ramp-timeout", type=int, default=300, help="seconds the autoscaler gets to reach its maximum (default 300)")
parser.add_argument("--hpa-wait", type=int, default=90, help="stop if the autoscaler still has no CPU number this many seconds after it was created (default 90)")
parser.add_argument("--drain-timeout", type=int, default=240, help="seconds to wait for the processors to store the backlog (default 240)")
parser.add_argument("--cooldown", type=int, default=0, help="afterwards, watch up to this many seconds for the processors to scale back down (default 0: do not wait)")
parser.add_argument("--max-messages", type=int, default=1500000, help="stop the load after the producers sent this many messages in total (default 1500000)")
parser.add_argument("--max-minutes", type=int, default=25, help="stop the whole test after this many minutes (default 25)")
parser.add_argument("--no-latency", action="store_true", help="do not send the tagged events that measure the end-to-end delay")
parser.add_argument("--out", default="evidence", help="folder for the result files (default: evidence)")
parser.add_argument("--prefix", default="step7-scale", help="start of the result file names (default: step7-scale)")
parser.add_argument("--host", help="address of the node to call for the REST API (default: the server, from Terraform)")
parser.add_argument("--port", type=int, help="node port (default: rest_node_port in infra/config.yml)")
args = parser.parse_args()

out_dir = Path(args.out) if Path(args.out).is_absolute() else lib.ROOT / args.out
host = port = None
topic = group = None
began = time.monotonic()
lock = threading.Lock()
samples = []              # one dict per sample, as in report.COLUMNS
phases = []               # {"name", "t0", "t1"}: what the test was doing, for the chart
windows = {}              # {"before": {"t0", "t1"}, "after": {...}}: the two measuring windows
probes = {}               # {"before": {...}, "after": {...}}: the tagged events
raw_log = []              # every producer rate line, in the order it was first seen
abort = threading.Event()
abort_reason = []
load_active = False
load_started = False
hpa_saved = False       # the autoscaler's own account (kubectl describe hpa) is in the evidence folder
baseline = {}


def now():
    return time.monotonic() - began


def say(text):
    with lock:
        print(text, flush=True)


# A number for the progress lines: rounded, with thousands separators, or a dash when there is none.
def shown(value, unit=""):
    return "-" if value is None else format(value, ",.0f") + unit


def stop_test(reason):
    if not abort.is_set():
        abort_reason.append(reason)
        abort.set()
        say("\n  !!! " + reason + "\n  !!! The load is being removed.\n")


def mark(name):
    with lock:
        if phases and phases[-1].get("t1") is None:
            phases[-1]["t1"] = round(now(), 1)

        phases.append({"name": name, "t0": round(now(), 1), "t1": None})


def current_phase():
    return phases[-1]["name"] if phases else "start"


def sleep_for(seconds):
    end = time.monotonic() + seconds

    while time.monotonic() < end and not abort.is_set():
        time.sleep(max(0.05, min(0.5, end - time.monotonic())))


# Wait until check() is true. Returns False when the time is up or the test must stop.
def wait_until(check, timeout, interval=1.0):
    end = time.monotonic() + timeout

    while time.monotonic() < end:
        if abort.is_set():
            return False

        if check():
            return True

        time.sleep(interval)

    return False


# For how many seconds nothing has changed in the number of pods (producers and processors, ready and wanted).
def steady_for():
    with lock:
        rows = list(samples)

    if not rows:
        return 0.0

    last, since = rows[-1], rows[-1]["t"]

    for r in reversed(rows):
        if (r["processor_pods_ready"] == last["processor_pods_ready"] == r["processor_pods_wanted"]
                and r["producer_pods_ready"] == last["producer_pods_ready"] == r["producer_pods_wanted"]):
            since = r["t"]
        else:
            break

    return last["t"] - since


# The processors have an autoscaler of their own, and it reacts to the load too. Measure only when it has finished.
def wait_steady(limit):
    if wait_until(lambda: steady_for() >= args.settle, limit, interval=1):
        row = latest()
        say("    the number of pods has not changed for " + str(args.settle) + " s (producers " + str(row["producer_pods_ready"]) + ", processors " + str(row["processor_pods_ready"]) + ")")
    elif not abort.is_set():
        say("    the number of pods was still changing after " + str(limit) + " s; measuring anyway")


def latest(key=None):
    with lock:
        if key is None:
            return dict(samples[-1]) if samples else None

        for row in reversed(samples):
            if row.get(key) is not None:
                return row[key]

    return None


# ---------------------------------------------------------------- the sampler: a thread that measures every few seconds

class Sampler(threading.Thread):
    def __init__(self, writer, handle):
        super().__init__(daemon=True)
        self.writer, self.handle = writer, handle
        self.stopping = threading.Event()
        self.pods = {}            # producer pod name -> {"sent": rows sent so far, "rate": msg/s it reported, "seen": when}
        self.restarts_at_start = None
        self.previous = None      # (t, records in MongoDB) of the sample before
        self.kafka_every = max(1, round(args.lag_every / args.interval))
        self.failures = 0
        self.agents, self.servers = set(), set()
        self.placement = {}

    def run(self):
        tick, due = 0, time.monotonic()

        while not self.stopping.is_set():
            try:
                self.take(tick)
                self.failures = 0
            except lib.Fail as problem:
                self.failures += 1
                say("  (a sample failed: " + str(problem).splitlines()[0][:150] + ")")

                if self.failures >= 6:
                    stop_test("the cluster has not answered for " + str(self.failures) + " samples in a row")
            except Exception:
                traceback.print_exc()
                self.failures += 1

            tick += 1
            due += args.interval
            wait = due - time.monotonic()

            if wait <= 0:
                due = time.monotonic()      # a sample took longer than the interval: do not try to catch up
            else:
                self.stopping.wait(wait)

    def take(self, tick):
        t = now()
        items = lib.kube_json("-n", lib.NS, "get", "pods,hpa")["items"]
        pods = [i for i in items if i["kind"] == "Pod"]
        hpas = {i["metadata"]["name"]: i for i in items if i["kind"] == "HorizontalPodAutoscaler"}
        labels = lambda p: p["metadata"].get("labels", {})
        load = [p for p in pods if labels(p).get("app.kubernetes.io/component") == "load-generator"]
        processors = [p for p in pods if labels(p).get("app.kubernetes.io/name") == "processor" and labels(p).get("app.kubernetes.io/part-of") == "ca2-pipeline"]
        alive = lambda group_: [p for p in group_ if not p["metadata"].get("deletionTimestamp")]

        # What the autoscaler wants. Until it has CPU numbers it reports 0, which is not a wish, so then the pods that exist count.
        def wanted(hpa_name, group_):
            asked = (hpas.get(hpa_name, {}).get("status") or {}).get("desiredReplicas")
            return asked if asked else len(alive(group_))

        def cpu(hpa_name):
            for metric in (hpas.get(hpa_name, {}).get("status", {}).get("currentMetrics") or []):
                if metric.get("type") == "Resource" and metric["resource"]["name"] == "cpu":
                    return metric["resource"]["current"].get("averageUtilization")

            return None

        # Restarts since the test began. A pod that was not there at the start counts from zero.
        counts = {}

        for p in pods:
            counts[p["metadata"]["name"]] = sum(c.get("restartCount", 0) for c in p.get("status", {}).get("containerStatuses", []))

        if self.restarts_at_start is None:
            self.restarts_at_start = dict(counts)

        restarted = {n: c - self.restarts_at_start.get(n, 0) for n, c in counts.items() if c - self.restarts_at_start.get(n, 0) > 0}

        if load_active:
            for name in STORES:
                if restarted.get(name):
                    stop_test(name + " restarted during the test (" + str(restarted[name]) + " time): the load was too much for it")

        self.placement = {}

        for p in alive(load):
            node = p.get("spec", {}).get("nodeName") or "(not placed yet)"
            self.placement[node] = self.placement.get(node, 0) + 1

        # The producers' own numbers. Each pod prints "sent N rows total, R msg/s" every 5 seconds. A pod's rate counts while its
        # newest line is recent, so pods that were removed drop out of the sum, and the totals of removed pods are kept.
        code, text, err = lib.kubectl("-n", lib.NS, "logs", "-l", LOAD_SELECTOR, "-c", "producer", "--tail=2", "--prefix", "--max-log-requests=10", timeout=40)

        for line in text.splitlines():
            found = RATE_LINE.match(line)

            if found:
                name, sent, rate = found.group(1), int(found.group(2)), int(found.group(3))
                seen = self.pods.setdefault(name, {"sent": 0, "rate": 0, "seen": None})

                if sent > seen["sent"]:
                    seen.update(sent=sent, rate=rate, seen=t)
                    raw_log.append("+" + format(t, ".0f").rjust(4) + " s  " + line.strip())

        current = {p["metadata"]["name"] for p in alive(load)}
        fresh = [v for name, v in self.pods.items() if name in current and v["seen"] is not None and t - v["seen"] <= 2.4 * 5]
        sent_total = sum(v["sent"] for v in self.pods.values())

        # Records stored in MongoDB, from the REST API.
        status, body = lib.rest_get(host, port, "/stats", timeout=4)
        docs = None

        try:
            docs = int(json.loads(body)["documents"]) if status == 200 else None
        except (ValueError, KeyError):
            docs = None

        # CPU of the nodes.
        agent_cpu = server_cpu = None
        code, text, err = lib.kubectl("top", "nodes", "--no-headers", timeout=30)
        agents, servers = [], []

        for line in text.splitlines():
            parts = line.split()

            if len(parts) >= 3 and re.fullmatch(r"\d+%", parts[2]):
                (agents if parts[0] in self.agents else servers if parts[0] in self.servers else []).append(int(parts[2][:-1]))

        if agents:
            agent_cpu = sum(agents) / len(agents)

        if servers:
            server_cpu = sum(servers) / len(servers)

        # The backlog, from Kafka itself. It costs a few seconds of CPU inside the Kafka pod, so it is not done at every sample.
        kafka_end = kafka_lag = None
        every = 2 if current_phase() in ("cleanup", "drain") else self.kafka_every

        if tick % every == 0 and group:
            rows = lib.kafka_group_lag(group)

            if rows and all(r[2] is not None for r in rows):
                kafka_end = sum(r[2] for r in rows)

                if all(r[3] is not None for r in rows):
                    kafka_lag = sum(r[3] for r in rows)

        row = {"t": round(t, 1), "clock": datetime.now(timezone.utc).strftime("%H:%M:%S"), "phase": current_phase(),
               "producer_pods_ready": len([p for p in load if lib.pod_is_ready(p)]), "producer_pods_wanted": wanted("producer-load", load), "producer_cpu_pct": cpu("producer-load"),
               "processor_pods_ready": len([p for p in processors if lib.pod_is_ready(p)]), "processor_pods_wanted": wanted("processor", processors), "processor_cpu_pct": cpu("processor"),
               "producers_msg_s": sum(v["rate"] for v in fresh) if self.pods else None, "sent_total": sent_total, "docs": docs,
               "kafka_end": kafka_end, "kafka_lag": kafka_lag,
               "agent_cpu_pct": None if agent_cpu is None else round(agent_cpu, 1), "server_cpu_pct": None if server_cpu is None else round(server_cpu, 1),
               "restarts": sum(restarted.values())}

        with lock:
            samples.append(row)
            self.writer.writerow(row)
            self.handle.flush()

        # What is printed: the latest numbers on one line.
        stored = None

        if docs is not None and self.previous and t - self.previous[0] > 0:
            stored = (docs - self.previous[1]) / (t - self.previous[0])

        if docs is not None:
            self.previous = (t, docs)

        pods_text = str(row["producer_pods_ready"]) + "/" + str(row["producer_pods_wanted"]) + " pods"
        pods_text += " (cpu " + format(row["producer_cpu_pct"], ".0f") + "%)" if row["producer_cpu_pct"] is not None else ""
        say("  " + format(t, ".0f").rjust(4) + " s  " + row["phase"].ljust(7) + " producers " + pods_text.ljust(21)
            + " send " + shown(row["producers_msg_s"]).rjust(6) + " msg/s"
            + "   stored " + shown(stored).rjust(6) + " msg/s"
            + "   backlog " + shown(latest("kafka_lag")).rjust(9)
            + "   processors " + str(row["processor_pods_ready"]) + "/" + str(row["processor_pods_wanted"])
            + "   workers cpu " + shown(agent_cpu, "%"))

        # The safety limits.
        if load_active:
            produced = max(sent_total, (kafka_end - baseline["kafka_end"]) if (kafka_end is not None and baseline.get("kafka_end") is not None) else 0)

            if produced > args.max_messages:
                stop_test("the producers sent " + format(produced, ",") + " messages, more than --max-messages " + format(args.max_messages, ","))

            if t > args.max_minutes * 60:
                stop_test("the test ran for more than " + str(args.max_minutes) + " minutes (--max-minutes)")


# ---------------------------------------------------------------- starting, measuring and removing the load

# The backlog right now, asked from Kafka (a very large number when Kafka does not answer).
def backlog():
    rows = lib.kafka_group_lag(group)
    return sum(r[3] or 0 for r in rows) if rows else 10 ** 9


def load_objects():
    code, out, err = lib.kubectl("-n", lib.NS, "get", "deployment/producer-load", "hpa/producer-load", "--ignore-not-found", "-o", "name")
    return out.split()


# Delete the autoscaler first, so that it cannot add pods while the Deployment goes away. Safe to run twice. A delete that fails
# (a hiccup of the connection) is tried again, up to three times, and one that fails does not stop the next from being tried.
def remove_load():
    global load_active
    load_active = False

    for command in (("delete", "-f", str(LOAD_DIR / "hpa.yaml")), ("delete", "-f", str(LOAD_DIR / "deployment-load.yaml")),
                    ("-n", lib.NS, "delete", "job", lib.JOB)):
        for attempt in range(3):
            try:
                code, out, err = lib.kubectl(*command, "--ignore-not-found", "--wait=false", "--request-timeout=20s", timeout=60)
            except lib.Fail as problem:
                code, err = 1, str(problem)

            if code == 0:
                break

            if attempt < 2:
                say("  (the delete did not work: " + (err.strip().splitlines() or ["no answer"])[0][:90] + "; trying again)")
                time.sleep(3)


# After the clean-up, ask the cluster once more, so that nobody believes a load is gone while it is still sending.
# Returns what is still there ("" when nothing is, None when the cluster did not answer).
def check_load_removed():
    try:
        code, out, err = lib.kubectl("-n", lib.NS, "get", "deployment/producer-load", "hpa/producer-load", "--ignore-not-found", "-o", "name", timeout=40)
    except lib.Fail as problem:
        code, out, err = 1, "", str(problem)

    if code != 0:
        say("  !!! Could not ask the cluster whether the load is gone (" + (err.strip().splitlines() or ["no answer"])[0][:120] + ").\n"
            "  !!! As soon as your connection works again, run:  make scale-down")
        return None

    if out.split():
        say("  !!! Still on the cluster: " + ", ".join(out.split()) + ".\n  !!! Run:  make scale-down")

    return " ".join(out.split())


# What the autoscalers say about themselves (conditions and the log of their decisions). Saved before they are deleted, also when the test stops early.
def save_hpa_evidence():
    global hpa_saved
    code, text, err = lib.kubectl("-n", lib.NS, "describe", "hpa", timeout=60)
    (out_dir / (args.prefix + "-hpa.txt")).write_text("$ kubectl -n " + lib.NS + " describe hpa\n" + (text if code == 0 else "(kubectl failed: " + err.strip() + ")\n"))
    hpa_saved = True


# The REST API finds a tagged event with find_one({"trace_id": ...}). Without an index MongoDB reads every stored record to answer that:
# harmless with 5,000 records, slow with the few hundred thousand that this test adds (and later tests would pay for it too). A sparse
# index holds only the records that carry a trace_id (the tagged events), so the load adds nothing to it. Running this twice changes nothing.
# The user name and password are read from the mounted Secret by the shell inside the pod, as in ca2lib.mongo_lookup.
def ensure_trace_index():
    database, collection = lib.config_value("pipeline-config", "MONGO_DB"), lib.config_value("pipeline-config", "MONGO_COLLECTION")
    script = ('const c = db.getSiblingDB("' + database + '").getCollection("' + collection + '"); '
              'const had = c.getIndexes().some(i => i.name === "trace_id_sparse"); '
              'if (!had) { c.createIndex({trace_id: 1}, {sparse: true, name: "trace_id_sparse"}); } '
              'print(JSON.stringify({had: had, total: Number(c.estimatedDocumentCount())}))')
    shell = 'mongosh --quiet -u "$(cat /etc/secrets/mongo/username)" -p "$(cat /etc/secrets/mongo/password)" --authenticationDatabase admin --eval "$1"'
    code, out, err = lib.kubectl("-n", lib.NS, "exec", "mongo-0", "-c", "mongo", "--", "sh", "-c", shell, "sh", script, timeout=120)

    for line in reversed(out.splitlines()):
        if line.startswith("{"):
            return not json.loads(line)["had"]

    raise lib.Fail("mongosh in mongo-0 could not create the index on trace_id. Without it the tagged-event timings would measure MongoDB reading every record."
                   " To run the test without the tagged events:  make scale-test SCALE_ARGS=\"--no-latency\"\nexit code " + str(code) + "\n" + (err.strip() or out.strip() or "no output")[-600:])


def preflight():
    global host, port, topic, group
    say("[1] Checks before anything is started")
    problems = lib.stack_problems()

    if problems:
        raise lib.Fail("the stack is not ready: " + ", ".join(problems) + ". Run:  make status   (and  make diagnose  if a pod is stuck)")

    topic, group = lib.config_value("pipeline-config", "KAFKA_TOPIC"), lib.config_value("processor-config", "CONSUMER_GROUP")
    host, port = lib.rest_target(args.host, args.port)
    status, body = lib.rest_get(host, port, "/stats")

    if status != 200:
        raise lib.Fail("the REST API does not answer from this computer (" + str(status) + " " + body.strip()[:100] + "). If your internet address changed (new Wi-Fi, a hotspot, a VPN), run:  make cluster-up")

    baseline["docs"] = int(json.loads(body)["documents"])

    if not args.no_latency:
        made = ensure_trace_index()
        summary_extra["trace_index"] = "created" if made else "existed"
        say("    MongoDB: " + ("created an index on trace_id" if made else "the index on trace_id exists") + " (only tagged events are in it), so finding a tagged event stays fast however many records the load adds")

    code, text, err = lib.kubectl("top", "nodes", "--no-headers", timeout=40)

    if code != 0 or not text.strip():
        raise lib.Fail("there are no CPU numbers yet, so the autoscaler could not work (" + (err.strip() or "empty answer") + "). metrics-server needs about a minute after the cluster starts. Try:  kubectl top nodes")

    left = load_objects()

    if left:
        say("    removing what an earlier run left behind: " + ", ".join(left))
        remove_load()
        wait_until(lambda: not lib.kubectl("-n", lib.NS, "get", "pods", "-l", LOAD_SELECTOR, "-o", "name")[1].strip(), 90)

    nodes = lib.kube_json("get", "nodes")["items"]

    for n in nodes:
        (sampler.servers if n["metadata"].get("labels", {}).get("ca2/role") == "server" else sampler.agents).add(n["metadata"]["name"])

    rows = lib.kafka_group_lag(group)

    if rows and all(r[2] is not None for r in rows):
        baseline["kafka_end"] = sum(r[2] for r in rows)
        lag = sum(r[3] for r in rows if r[3] is not None)

        if lag > 2000:
            say("    the processors are still " + format(lag, ",") + " messages behind: waiting up to 2 minutes for them to catch up")
            wait_until(lambda: backlog() <= 2000, 120, interval=10)
    else:
        say("    (Kafka's own numbers are not available: the backlog columns will stay empty)")

    say("    stack is ready; " + format(baseline["docs"], ",") + " records are in MongoDB; the REST API answers; " + str(len(sampler.agents)) + " worker nodes and " + str(len(sampler.servers)) + " server node report CPU")


# One tagged event: how long from "Kafka has it" until "the REST API returns it".
def probe(label, wait_limit):
    trace = lib.new_trace_id()

    try:
        sent = lib.send_event(trace, timeout=240)
    except lib.Fail as problem:
        return {"seconds": None, "note": "the tagged event could not be sent: " + str(problem).splitlines()[0][:150]}

    start = time.monotonic()
    say("      [" + label + "] tagged event " + trace + " is in Kafka (partition " + str(sent["partition"]) + ", offset " + str(sent["offset"]) + "); waiting for the REST API to return it")

    while time.monotonic() - start < wait_limit:
        if lib.rest_get(host, port, "/events/" + trace, timeout=5)[0] == 200:
            took = time.monotonic() - start
            say("      [" + label + "] the REST API returned it after " + format(took, ".1f") + " s")
            return {"seconds": round(took, 2), "trace_id": trace, "partition": sent["partition"], "offset": sent["offset"]}

        if abort.is_set():
            break

        time.sleep(1)

    return {"seconds": None, "trace_id": trace, "note": "the event was not returned within " + str(wait_limit) + " s"}


def start_probe(label, wait_limit):
    if args.no_latency:
        return None

    def work():
        probes[label] = probe(label, wait_limit)

    thread = threading.Thread(target=work, daemon=True)
    thread.start()

    return thread


def measure(name, seconds):
    mark(name)
    t0 = now()
    say("\n    measuring '" + name + "' for " + str(seconds) + " s")
    thread = None if (name == "after" and summary_extra.get("skip_after_probe")) else start_probe(name, 90 if name == "before" else 600)
    sleep_for(seconds)
    windows[name] = {"t0": round(t0, 1), "t1": round(now(), 1)}

    if name == "after":
        return thread

    if thread:
        thread.join(timeout=120)

        if thread.is_alive():
            probes["before"] = {"seconds": None, "note": "the first tagged event did not get through within 120 s"}
            summary_extra["skip_after_probe"] = True

    return None


def ramp():
    mark("ramp")
    began_ramp = now()
    say("\n[3] Creating the autoscaler (producer/scaling/hpa.yaml). From here Kubernetes decides how many producer pods run.")
    code, out, err = lib.kubectl("apply", "-f", str(LOAD_DIR / "hpa.yaml"))

    if code != 0:
        raise lib.Fail("could not create the autoscaler:\n" + err.strip())

    h = lib.kube_json("-n", lib.NS, "get", "hpa", "producer-load")
    wanted = h["spec"]["maxReplicas"]
    summary_extra["hpa"] = {"min": h["spec"].get("minReplicas", 1), "max": wanted}

    for metric in h["spec"].get("metrics", []):
        if metric.get("type") == "Resource":
            summary_extra["hpa"]["cpu_target"] = metric["resource"]["target"].get("averageUtilization")

    try:
        p = lib.kube_json("-n", lib.NS, "get", "hpa", "processor")
        summary_extra["hpa"].update(processor_min=p["spec"].get("minReplicas", 1), processor_max=p["spec"]["maxReplicas"],
                                    processor_target=p["spec"]["metrics"][0]["resource"]["target"].get("averageUtilization"))
    except (lib.Fail, KeyError, IndexError):
        pass

    say("    waiting until " + str(wanted) + " producer pods are ready (at most " + str(args.ramp_timeout) + " s)")

    def reached():
        row = latest()

        if row and row["t"] - began_ramp > args.hpa_wait and row["producer_cpu_pct"] is None and row["producer_pods_ready"] <= 1:
            raise lib.Fail("after " + str(args.hpa_wait) + " s the autoscaler still has no CPU number for the producer pods, so it cannot scale. What it said about itself is in " + args.out + "/" + args.prefix + "-hpa.txt (look for ScalingActive and FailedGetResourceMetric)")

        return row and row["producer_pods_ready"] >= wanted

    if wait_until(reached, args.ramp_timeout):
        summary_extra["reached_max_after"] = round(now() - began_ramp, 1)
        say("    all " + str(wanted) + " producer pods are ready " + format(now() - began_ramp, ".0f") + " s after the autoscaler was created")
    elif not abort.is_set():
        row = latest()
        say("    the time is up with " + str(row["producer_pods_ready"] if row else "?") + " of " + str(wanted) + " producer pods ready: the second window measures what the autoscaler reached")


def drain(probe_thread):
    if baseline.get("kafka_end") is None:
        say("\n[5] Kafka's own numbers are not available, so the test cannot see the backlog. Check later with:  make logs")
        return

    mark("drain")
    say("\n[5] Waiting for the processors to store the backlog (at most " + str(args.drain_timeout) + " s)")

    def caught_up():
        lag = latest("kafka_lag")
        return lag is not None and lag <= 1000 and not (probe_thread and probe_thread.is_alive())

    if wait_until(caught_up, args.drain_timeout, interval=2):
        say("    the backlog is gone")
    else:
        lag = latest("kafka_lag")
        summary_extra["backlog_left"] = lag
        say("    the time is up; the backlog is " + (format(lag, ",") if lag is not None else "unknown") + " messages. The processors keep working on it. To clear it at once:  make down  and  make up")


def cooldown():
    if args.cooldown <= 0 or abort.is_set():
        return

    mark("cooldown")
    smallest = summary_extra.get("hpa", {}).get("processor_min", 1)
    say("\n[6] Watching the processors scale back down (at most " + str(args.cooldown) + " s)")

    if wait_until(lambda: (latest("processor_pods_ready") or 0) <= smallest, args.cooldown, interval=2):
        say("    the processors are back to " + str(smallest) + " pod")
    else:
        say("    the time is up with " + str(latest("processor_pods_ready")) + " processor pods; the autoscaler removes one pod at a time and waits before it starts")


summary_extra = {}
problem_text = None
finished = False
sampler = None
load_left = False         # True when the load may still be on the cluster after the clean-up

try:
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_handle = open(out_dir / (args.prefix + "-samples.csv"), "w", newline="")
    csv_writer = csv.DictWriter(csv_handle, fieldnames=report.COLUMNS)
    csv_writer.writeheader()
    sampler = Sampler(csv_writer, csv_handle)
    started_text = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    print("Scaling test: 1 producer pod, then the autoscaler (namespace " + lib.NS + ")")
    print("Limits: " + format(args.max_messages, ",") + " messages, " + str(args.max_minutes) + " minutes, any restart of kafka-0 or mongo-0. Ctrl-C stops the test and removes the load.\n")

    preflight()
    sampler.start()
    mark("warmup")
    load_active = load_started = True
    say("\n[2] Starting ONE producer pod (producer/scaling/deployment-load.yaml). There is no autoscaler yet.")
    code, out, err = lib.kubectl("apply", "-f", str(LOAD_DIR / "deployment-load.yaml"))

    if code != 0:
        raise lib.Fail("could not create the load generator:\n" + err.strip())

    if not wait_until(lambda: (latest("producer_pods_ready") or 0) >= 1, 180):
        raise lib.Fail("the producer pod was not ready within 180 s. Run:  make diagnose")

    wait_steady(args.settle_limit)
    measure("before", args.before)

    if not abort.is_set():
        ramp()

    if not abort.is_set():
        mark("settle")
        wait_steady(args.settle_limit)

    after_probe = None

    if not abort.is_set():
        after_probe = measure("after", args.after)
        summary_extra["load_pods_per_node"] = dict(sampler.placement)

    save_hpa_evidence()
    mark("cleanup")
    say("\n[4] Removing the load and the autoscaler")
    remove_load()

    if not abort.is_set():
        drain(after_probe)
        cooldown()
        finished = True
except KeyboardInterrupt:
    problem_text = "stopped by the user (Ctrl-C)"
    print("\n  Stopped. Removing the load...")
except lib.Fail as problem:
    problem_text = str(problem)
except Exception:
    traceback.print_exc()
    problem_text = "the test itself broke (see the error above). Paste it to your assistant."
finally:
    try:
        if load_started:
            if not hpa_saved:
                try:
                    save_hpa_evidence()
                except Exception:
                    pass

            remove_load()
            load_left = check_load_removed() != ""
    except Exception as error:
        load_left = True
        print("  Could not remove the load: " + str(error) + "\n  Run:  make scale-down")

    if sampler is not None:
        sampler.stopping.set()

        if sampler.is_alive():
            sampler.join(timeout=60)

    mark("end")

    if abort_reason:
        problem_text = problem_text or abort_reason[0]

# ---------------------------------------------------------------- the files and the table

try:
    csv_handle.close()
except Exception:
    pass

if samples:
    (out_dir / (args.prefix + "-producer-log.txt")).write_text("The lines the load-generating producer pods printed (kubectl logs), in the order the test saw them.\n\n" + "\n".join(raw_log) + "\n")
    summary = {"started": started_text, "args": vars(args), "phases": phases, "windows": windows, "probes": probes, "aborted": problem_text,
               "baseline": baseline, "samples": len(samples)}
    summary.update(summary_extra)
    (out_dir / (args.prefix + "-summary.json")).write_text(json.dumps(summary, indent=2) + "\n")
    print("\n" + report.render(str(out_dir), args.prefix))
    print("Files in " + args.out + "/:  " + ", ".join(args.prefix + s for s in ("-samples.csv", "-summary.json", "-results.md", "-chart.svg", "-hpa.txt", "-producer-log.txt")))

if finished and not problem_text:
    before, after = report.window_numbers(samples, windows["before"]["t0"], windows["before"]["t1"]), report.window_numbers(samples, windows["after"]["t0"], windows["after"]["t1"])
    print("\nRESULT: DONE - producers sent " + report.num(before["producers_msg_s"]) + " msg/s with " + report.pods(before["producer_pods"]) + " pod and "
          + report.num(after["producers_msg_s"]) + " msg/s with " + report.pods(after["producer_pods"]) + " pods; MongoDB stored " + report.num(before["stored_msg_s"]) + " and "
          + report.num(after["stored_msg_s"]) + " msg/s" + ((". The processors were still " + format(summary_extra["backlog_left"], ",") + " messages behind when the test ended; they keep working on it.") if summary_extra.get("backlog_left") else ""))
else:
    print("\nRESULT: STOPPED EARLY - " + (problem_text or "the test did not finish"))

    if load_left:
        print("WARNING: the load may still be running on the cluster. Run:  make scale-down")

    sys.exit(1)

if load_left:
    print("WARNING: the load may still be running on the cluster. Run:  make scale-down")
    sys.exit(1)
