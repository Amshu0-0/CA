# Scaling test: what happens when the load goes up and the autoscalers react, and when the load stops again.
#
#   make scale-test                                          the whole test, about 7 minutes
#   make scale-test SCALE_ARGS="--before 30 --after 30"      shorter measuring windows
#   make scale-report                                        draw the table and the chart again from the saved files (needs no cluster)
#   make scale-down                                          switch the load off by hand (the test does it by itself, also when it stops early)
#
# Everything this program prints is also saved in <prefix>-run.txt (no "| tee" needed). Ctrl-C, closing the Terminal window and "kill" all
# take the same way out: the load is switched off and checked, then the files and the table are written.
#
# The load generator (Deployment producer-load) and its autoscaler are part of the stack and idle until this test switches the load on.
# The three folders producer/load, producer/loadtest-one and producer/loadtest differ only in PRODUCER_IDLE and in the autoscaler's maximum;
# "kubectl apply -k <folder>" is the whole switch.
#
# Steps:
#   1. Check that the stack runs, that the REST API answers from this computer, and that CPU numbers are available.
#   2. Load on, ONE producer pod (producer/loadtest-one: the autoscaler may not add pods). Measure it for a while. This is "before".
#   3. Let the autoscaler work (producer/loadtest: the same load, maximum 5 pods). Kubernetes adds pods by itself until the CPU is low enough.
#      Measure the larger set of producer pods. This is "after".
#   4. Load off (producer/load). Watch the producers shrink back to one pod, the backlog in Kafka disappear, and the processors shrink back.
#      The time each of them takes is the "scale back down" part of the assignment. (Also when the test stops early or you press Ctrl-C.)
#
# While it runs, a second thread takes a sample every 5 seconds and appends it to <prefix>-samples.csv:
#   pods and autoscaler decisions   kubectl get pods,hpa
#   messages per second per pod     the producers print one JSON line every 5 seconds (kubectl logs)
#   records stored in MongoDB       GET /stats on the REST API, called from this computer (this also times the REST API)
#   backlog (consumer lag)          kafka-consumer-groups inside the Kafka pod, every 20 seconds
#   CPU of the nodes and pods       kubectl top nodes / kubectl top pods
# Every 15 seconds the plain output of "kubectl get hpa" is appended to <prefix>-hpa-over-time.txt: the assignment's evidence.
# In each measuring window one more tagged event (the same one as the smoke test) is sent, and the test times how long it takes to
# get from Kafka to the REST API. That is the delay the pipeline shows under that load.
#
# The processors have an autoscaler too (it is part of the stack). Even one producer keeps them busy, so they scale up in the first minute.
# The test waits until the number of pods has stopped changing before it starts a measuring window.
#
# The producers send as fast as their CPU limit allows, and a message is about 2.3 KB, so the test protects the disks of the cluster:
# it stops the load when the producers have sent --max-messages messages in total (1,000,000 are about 2.3 GB in Kafka), when a Kafka or
# MongoDB pod restarts, or after --max-minutes. A normal run sends roughly 400,000 to 900,000.

import argparse
import csv
import json
import re
import signal
import sys
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

import ca3lib as lib
import scalereport as report

FOLDERS = {"one": "producer/loadtest-one", "on": "producer/loadtest", "off": "producer/load"}
LOAD_SELECTOR = "app.kubernetes.io/name=producer,app.kubernetes.io/component=load-generator"
# The line a producer prints every 5 seconds in replay-forever mode (kubectl logs --prefix puts the pod name in front), for example:
# [pod/producer-load-6d9f8b-x2k4j/producer] {"ts":"...","level":"info","service":"producer","msg":"rate","rows_total":21480,"rate_msg_s":804,"topic":"network-flows"}
RATE_LINE = re.compile(r"^\[pod/(\S+?)/producer\] (\{.*\})\s*$")
STORES = ("kafka-0", "mongo-0")

parser = argparse.ArgumentParser(description="Measure the pipeline with one producer pod, with the autoscaler at work, and while everything shrinks back.")
parser.add_argument("--before", type=int, default=45, help="seconds to measure with one producer pod (default 45)")
parser.add_argument("--after", type=int, default=60, help="seconds to measure with the autoscaled producers (default 60)")
parser.add_argument("--settle", type=int, default=20, help="seconds the number of pods must stay unchanged before a window starts (default 20)")
parser.add_argument("--settle-limit", type=int, default=240, help="the longest time to wait for that (default 240)")
parser.add_argument("--interval", type=float, default=5.0, help="seconds between samples (default 5)")
parser.add_argument("--lag-every", type=float, default=20.0, help="seconds between asking Kafka for the backlog (default 20; it costs the Kafka pod a little CPU)")
parser.add_argument("--cpu-every", type=float, default=10.0, help="seconds between asking for the CPU numbers (default 10; the numbers only change every 15 seconds)")
parser.add_argument("--hpa-every", type=float, default=15.0, help="seconds between the 'kubectl get hpa' snapshots in the evidence file (default 15)")
parser.add_argument("--ramp-timeout", type=int, default=300, help="seconds the autoscaler gets to reach its maximum (default 300)")
parser.add_argument("--hpa-wait", type=int, default=90, help="stop if the autoscaler still has no CPU number this many seconds after the load started (default 90)")
parser.add_argument("--shrink-timeout", type=int, default=300, help="seconds to wait for the producers to shrink back after the load is off (default 300)")
parser.add_argument("--drain-timeout", type=int, default=300, help="seconds to wait for the processors to store the backlog (default 300)")
parser.add_argument("--cooldown", type=int, default=420, help="seconds to wait for the processors to shrink back (default 420; 0 = do not wait)")
parser.add_argument("--max-messages", type=int, default=1000000, help="stop the load after the producers sent this many messages in total (default 1000000)")
parser.add_argument("--max-minutes", type=int, default=30, help="stop the whole test after this many minutes (default 30)")
parser.add_argument("--no-latency", action="store_true", help="do not send the tagged events that measure the delay (and do not create the index in MongoDB)")
parser.add_argument("--out", default="evidence", help="folder for the result files (default: evidence)")
parser.add_argument("--prefix", default="step6-scale", help="start of the result file names (default: step6-scale)")
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
baseline = {}
summary_extra = {}
hpa_log = None


def now():
    return time.monotonic() - began


# Everything the test prints also goes into <prefix>-run.txt, so nobody needs "| tee": a pipe would die on Ctrl-C and cut off the clean-up.
# A closed window or a broken pipe must never stop the clean-up either, so a failing write to the screen is ignored from then on.
class Tee:
    def __init__(self, stream, path):
        self.stream, self.file, self.broken = stream, open(path, "w"), False

    def write(self, text):
        try:
            self.file.write(text)
            self.file.flush()
        except Exception:
            pass

        if not self.broken:
            try:
                self.stream.write(text)
                self.stream.flush()
            except Exception:
                self.broken = True

        return len(text)

    def flush(self):
        pass

    def isatty(self):
        return False


stopped_by = []           # the signal that stopped the test, when it was not Ctrl-C


# Ctrl-C is SIGINT. Closing the Terminal window sends SIGHUP and "kill" sends SIGTERM: all three take the same way out, so the load is switched off.
def ignore_signals():
    for name in ("SIGINT", "SIGHUP", "SIGTERM"):
        try:
            signal.signal(getattr(signal, name), signal.SIG_IGN)
        except Exception:
            pass


def stop_on_signal(number, frame):
    ignore_signals()      # a second Ctrl-C, or the same signal passed on by make, must not break the clean-up
    stopped_by.append(signal.Signals(number).name)
    raise KeyboardInterrupt


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
        say("\n  !!! " + reason + "\n  !!! The load is being switched off.\n")


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


def latest(key=None):
    with lock:
        if key is None:
            return dict(samples[-1]) if samples else None

        for row in reversed(samples):
            if row.get(key) is not None:
                return row[key]

    return None


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


def millicores(text):
    text = text.strip()

    if text.endswith("m"):
        return float(text[:-1])

    if text.endswith("u"):
        return float(text[:-1]) / 1000.0

    return float(text) * 1000.0


# The CPU limit of a pod in millicores (the sum of its containers' limits), or None.
def cpu_limit(pod):
    total = 0.0

    for c in pod.get("spec", {}).get("containers", []):
        limit = c.get("resources", {}).get("limits", {}).get("cpu")

        if limit is None:
            return None

        total += millicores(str(limit))

    return total or None


# Which part of the pipeline a pod belongs to (the names of the CPU rows in the report), or None.
def program_of(pod):
    name = pod["metadata"]["name"]
    labels = pod["metadata"].get("labels", {})

    if name in ("kafka-0", "mongo-0"):
        return name.split("-")[0]

    if labels.get("app.kubernetes.io/part-of") != "ca3-pipeline":
        return None

    if labels.get("app.kubernetes.io/name") == "producer":
        return "producer" if labels.get("app.kubernetes.io/component") == "load-generator" else None

    return labels.get("app.kubernetes.io/name") if labels.get("app.kubernetes.io/name") in ("processor", "restapi") else None


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
        self.cpu_every = max(1, round(args.cpu_every / args.interval))
        self.hpa_every = max(1, round(args.hpa_every / args.interval))
        self.failures = 0
        self.agents, self.servers = set(), set()
        self.placement = {}
        self.last_cpu = {}

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

    # CPU of the nodes (percent) and of the five programs (percent of their CPU limit), from metrics-server. None where it does not answer.
    def read_cpu(self, pods):
        result = {"agent": None, "server": None}
        code, text, err = lib.kubectl("top", "nodes", "--no-headers", timeout=30)
        agents, servers = [], []

        for line in text.splitlines():
            parts = line.split()

            if len(parts) >= 3 and re.fullmatch(r"\d+%", parts[2]):
                (agents if parts[0] in self.agents else servers if parts[0] in self.servers else []).append(int(parts[2][:-1]))

        if agents:
            result["agent"] = sum(agents) / len(agents)

        if servers:
            result["server"] = sum(servers) / len(servers)

        code, text, err = lib.kubectl("-n", lib.NS, "top", "pods", "--no-headers", timeout=30)
        by_name = {p["metadata"]["name"]: p for p in pods}
        found = {}

        for line in text.splitlines():
            parts = line.split()

            if len(parts) >= 2 and parts[0] in by_name:
                pod = by_name[parts[0]]
                program, limit = program_of(pod), cpu_limit(pod)

                try:
                    used = millicores(parts[1])
                except ValueError:
                    continue

                if program and limit:
                    found.setdefault(program, []).append(100.0 * used / limit)

        for program in ("producer", "processor", "restapi", "kafka", "mongo"):
            result[program] = (sum(found[program]) / len(found[program])) if found.get(program) else None

        return result

    def take(self, tick):
        t = now()
        items = lib.kube_json("-n", lib.NS, "get", "pods,hpa")["items"]
        pods = [i for i in items if i["kind"] == "Pod"]
        hpas = {i["metadata"]["name"]: i for i in items if i["kind"] == "HorizontalPodAutoscaler"}
        labels = lambda p: p["metadata"].get("labels", {})
        load = [p for p in pods if labels(p).get("app.kubernetes.io/component") == "load-generator"]
        processors = [p for p in pods if labels(p).get("app.kubernetes.io/name") == "processor" and labels(p).get("app.kubernetes.io/part-of") == "ca3-pipeline"]
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

        # The producers' own numbers. Each pod prints a JSON line with msg "rate" every 5 seconds. A pod's rate counts while its
        # newest line is recent, so pods that were removed drop out of the sum, and the totals of removed pods are kept.
        code, text, err = lib.kubectl("-n", lib.NS, "logs", "-l", LOAD_SELECTOR, "-c", "producer", "--tail=2", "--prefix", "--max-log-requests=10", timeout=40)

        for line in text.splitlines():
            found = RATE_LINE.match(line)

            if not found:
                continue

            try:
                record = json.loads(found.group(2))
                sent, rate = int(record["rows_total"]), int(record["rate_msg_s"])
            except (ValueError, KeyError, TypeError):
                continue

            if record.get("msg") != "rate":
                continue

            seen = self.pods.setdefault(found.group(1), {"sent": 0, "rate": 0, "seen": None})

            if sent > seen["sent"]:
                seen.update(sent=sent, rate=rate, seen=t)
                raw_log.append("+" + format(t, ".0f").rjust(4) + " s  " + line.strip())

        current = {p["metadata"]["name"] for p in alive(load)}
        fresh = [v for name, v in self.pods.items() if name in current and v["seen"] is not None and t - v["seen"] <= 2.4 * 5]
        sent_total = sum(v["sent"] for v in self.pods.values())

        # Records stored in MongoDB, from the REST API. The call is timed: it is also the REST API's answer time seen from here.
        began_call = time.monotonic()
        status, body = lib.rest_get(host, port, "/stats", timeout=4)
        rest_ms = (time.monotonic() - began_call) * 1000.0
        docs = None

        try:
            docs = int(json.loads(body)["documents"]) if status == 200 else None
        except (ValueError, KeyError):
            docs = None

        # CPU of the nodes and of the programs.
        if tick % self.cpu_every == 0:
            self.last_cpu = self.read_cpu(pods)

            cpu_now = self.last_cpu
        else:
            cpu_now = {}

        # The backlog, from Kafka itself. It costs a few seconds of CPU inside the Kafka pod, so it is not done at every sample.
        kafka_end = kafka_lag = None
        every = 2 if current_phase() in ("shrink", "drain") else self.kafka_every

        if tick % every == 0 and group:
            rows = lib.kafka_group_lag(group)

            if rows and all(r[2] is not None for r in rows):
                kafka_end = sum(r[2] for r in rows)

                if all(r[3] is not None for r in rows):
                    kafka_lag = sum(r[3] for r in rows)

        r1 = lambda v: None if v is None else round(v, 1)
        row = {"t": round(t, 1), "clock": datetime.now(timezone.utc).strftime("%H:%M:%S"), "phase": current_phase(),
               "producer_pods_ready": len([p for p in load if lib.pod_is_ready(p)]), "producer_pods_wanted": wanted("producer-load", load), "producer_cpu_pct": cpu("producer-load"),
               "processor_pods_ready": len([p for p in processors if lib.pod_is_ready(p)]), "processor_pods_wanted": wanted("processor", processors), "processor_cpu_pct": cpu("processor"),
               "producers_msg_s": sum(v["rate"] for v in fresh) if self.pods else None, "sent_total": sent_total, "docs": docs,
               "kafka_end": kafka_end, "kafka_lag": kafka_lag,
               "agent_cpu_pct": r1(cpu_now.get("agent")), "server_cpu_pct": r1(cpu_now.get("server")),
               "producer_limit_pct": r1(cpu_now.get("producer")), "processor_limit_pct": r1(cpu_now.get("processor")), "restapi_limit_pct": r1(cpu_now.get("restapi")),
               "kafka_limit_pct": r1(cpu_now.get("kafka")), "mongo_limit_pct": r1(cpu_now.get("mongo")),
               "rest_ms": round(rest_ms, 1), "rest_ok": 1 if status == 200 else 0,
               "restarts": sum(restarted.values())}

        with lock:
            samples.append(row)
            self.writer.writerow(row)
            self.handle.flush()

        # What the autoscalers say, as kubectl prints it. This is the evidence file for "kubectl get hpa over time".
        if tick % self.hpa_every == 0 and hpa_log is not None:
            code, text, err = lib.kubectl("-n", lib.NS, "get", "hpa", timeout=30)
            append_hpa(t, row, text if code == 0 else "(kubectl get hpa failed: " + err.strip() + ")\n")

        # What is printed: the latest numbers on one line.
        stored = None

        if docs is not None and self.previous and t - self.previous[0] > 0:
            stored = (docs - self.previous[1]) / (t - self.previous[0])

        if docs is not None:
            self.previous = (t, docs)

        pods_text = str(row["producer_pods_ready"]) + "/" + str(row["producer_pods_wanted"]) + " pods"
        pods_text += " (cpu " + format(row["producer_cpu_pct"], ".0f") + "%)" if row["producer_cpu_pct"] is not None else ""
        head = "  " + format(t, ".0f").rjust(4) + " s  " + row["phase"].ljust(8) + " producers " + pods_text.ljust(21)

        if status == 200:
            say(head + " send " + shown(row["producers_msg_s"]).rjust(6) + " msg/s"
                + "   stored " + shown(stored).rjust(6) + " msg/s"
                + "   backlog " + shown(latest("kafka_lag")).rjust(9)
                + "   processors " + str(row["processor_pods_ready"]) + "/" + str(row["processor_pods_wanted"])
                + "   rest " + shown(rest_ms, " ms"))
        else:
            say(head + "   !!! the REST API did not answer (" + str(status) + ")")

        # The safety limits.
        if load_active:
            produced = max(sent_total, (kafka_end - baseline["kafka_end"]) if (kafka_end is not None and baseline.get("kafka_end") is not None) else 0)

            if produced > args.max_messages:
                stop_test("the producers sent " + format(produced, ",") + " messages, more than --max-messages " + format(args.max_messages, ","))

            if t > args.max_minutes * 60:
                stop_test("the test ran for more than " + str(args.max_minutes) + " minutes (--max-minutes)")


def append_hpa(t, row, text):
    with lock:
        hpa_log.write("=== " + datetime.now(timezone.utc).strftime("%H:%M:%S") + " UTC   +" + format(t, ".0f") + " s   phase: " + row["phase"]
                      + "   (producer pods ready " + str(row["producer_pods_ready"]) + ", processor pods ready " + str(row["processor_pods_ready"]) + ") ===\n" + text.rstrip() + "\n\n")
        hpa_log.flush()


# ---------------------------------------------------------------- switching the load, measuring and removing it

# The backlog right now, asked from Kafka (a very large number when Kafka does not answer).
def backlog():
    rows = lib.kafka_group_lag(group)
    return sum(r[3] or 0 for r in rows) if rows else 10 ** 9


# kubectl apply -k <folder>: "one", "on" or "off". A failure is an exception with kubectl's own message.
def switch(which):
    code, out, err = lib.kubectl("apply", "-k", str(lib.ROOT / FOLDERS[which]))

    if code != 0:
        raise lib.Fail("could not switch the load (" + FOLDERS[which] + "):\n" + err.strip())


# Is the load generator sending, and what is the maximum of its autoscaler? (idle, maximum), or None when the cluster does not answer.
def load_state():
    try:
        d = lib.kube_json("-n", lib.NS, "get", "deployment", "producer-load", timeout=40)
        h = lib.kube_json("-n", lib.NS, "get", "hpa", "producer-load", timeout=40)
    except lib.Fail:
        return None

    env = {e["name"]: e.get("value") for c in d["spec"]["template"]["spec"]["containers"] for e in c.get("env", [])}
    return env.get("PRODUCER_IDLE", "false") == "true", h["spec"]["maxReplicas"]


# Switch the load off. Safe to run twice. An apply that fails (a hiccup of the connection) is tried again, up to three times.
def switch_off():
    global load_active
    load_active = False

    for attempt in range(3):
        try:
            switch("off")
            return True
        except lib.Fail as problem:
            if attempt < 2:
                reason = [line for line in str(problem).splitlines() if line.strip()][-1].strip()[:100]
                say("  (switching the load off did not work: " + reason + "; trying again)")
                time.sleep(3)

    return False


# After the clean-up, ask the cluster once more, so that nobody believes the load is off while it is still sending.
# Returns True when the load is off, False when it is not, None when the cluster did not answer.
def check_load_off():
    state = load_state()

    if state is None:
        say("  !!! Could not ask the cluster whether the load is off. As soon as your connection works again, run:  make scale-down")
        return None

    if not state[0]:
        say("  !!! The load generator is still sending.  Run:  make scale-down")
        return False

    say("\n  Checked with the cluster: the load is off (the producer pods of the load generator are idle).")
    return True


# What the autoscalers say about themselves (conditions and the log of their decisions). Saved once, at the end of the test and before the load is
# switched off when the test stops early (so the file still shows what was wrong).
def save_hpa_evidence():
    code, text, err = lib.kubectl("-n", lib.NS, "describe", "hpa", timeout=20)
    (out_dir / (args.prefix + "-hpa.txt")).write_text("$ kubectl -n " + lib.NS + " describe hpa\n" + (text if code == 0 else "(kubectl failed: " + err.strip() + ")\n"))


# The REST API finds a tagged event with find_one({"trace_id": ...}). Without an index MongoDB reads every stored record to answer that:
# harmless with 5,000 records, slow with the few hundred thousand that this test adds (and later tests would pay for it too). A sparse
# index holds only the records that carry a trace_id (the tagged events), so the load adds nothing to it. Running this twice changes nothing.
# mongosh runs inside the MongoDB pod as the administrator (ca3lib.mongosh_json); the password never reaches this computer.
def ensure_trace_index():
    database, collection = lib.config_value("pipeline-config", "MONGO_DB"), lib.config_value("pipeline-config", "MONGO_COLLECTION")
    script = ('const c = db.getSiblingDB("' + database + '").getCollection("' + collection + '"); '
              'const had = c.getIndexes().some(i => i.name === "trace_id_sparse"); '
              'if (!had) { c.createIndex({trace_id: 1}, {sparse: true, name: "trace_id_sparse"}); } '
              'print(JSON.stringify({had: had, total: Number(c.estimatedDocumentCount())}))')

    try:
        answer = lib.mongosh_json(script, timeout=120)
    except lib.Fail as problem:
        raise lib.Fail("could not create the index on trace_id in MongoDB. Without it the tagged-event timings would measure MongoDB reading every record."
                       " To run the test without the tagged events:  make scale-test SCALE_ARGS=\"--no-latency\"\n" + str(problem))

    return not answer["had"]


def preflight():
    global host, port, topic, group
    say("[1] Checks before anything is started")
    problems = lib.stack_problems()

    if problems:
        raise lib.Fail("the stack is not ready: " + ", ".join(problems) + ". Run:  make status   (and  make diagnose  if a pod is stuck)")

    code, out, err = lib.kubectl("-n", lib.NS, "get", "deployment/producer-load", "hpa/producer-load", "hpa/processor", "--ignore-not-found", "-o", "name")

    if code != 0 or len(out.split()) != 3:
        raise lib.Fail("the load generator and the two autoscalers are not all in the stack (found: " + (" ".join(out.split()) or "nothing") + "). Run:  make up")

    topic, group = lib.config_value("pipeline-config", "KAFKA_TOPIC"), lib.config_value("processor-config", "CONSUMER_GROUP")
    host, port = lib.rest_target(args.host, args.port)
    status, body = lib.rest_get(host, port, "/stats")

    if status != 200:
        raise lib.Fail("the REST API does not answer from this computer (" + str(status) + " " + body.strip()[:100] + "). If your internet address changed (new Wi-Fi, a hotspot, a VPN), run  make cluster-up  (it updates the firewall)")

    baseline["docs"] = int(json.loads(body)["documents"])

    if not args.no_latency:
        made = ensure_trace_index()
        summary_extra["trace_index"] = "created" if made else "existed"
        say("    MongoDB: " + ("created an index on trace_id" if made else "the index on trace_id exists") + " (only tagged events are in it), so finding a tagged event stays fast however many records the load adds")

    code, text, err = lib.kubectl("top", "nodes", "--no-headers", timeout=40)

    if code != 0 or not text.strip():
        raise lib.Fail("there are no CPU numbers yet, so the autoscaler could not work (" + (err.strip() or "empty answer") + "). metrics-server needs about a minute after the cluster starts. Try:  kubectl top nodes")

    state = load_state()

    if state is None:
        raise lib.Fail("could not read the load generator from the cluster. Run:  make status")

    if not state[0] or state[1] != 5:
        say("    the load was left on (or the autoscaler left at " + str(state[1]) + ") by an earlier run: switching it off and waiting for one idle pod")
        switch("off")
        wait_until(lambda: len(lib.kube_json("-n", lib.NS, "get", "pods", "-l", LOAD_SELECTOR)["items"]) == 1, 120)

    nodes = lib.kube_json("get", "nodes")["items"]

    for n in nodes:
        (sampler.servers if n["metadata"].get("labels", {}).get("ca3/role") == "server" else sampler.agents).add(n["metadata"]["name"])

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
        note = "the tagged event could not be sent: " + str(problem).splitlines()[0][:150]
        say("      [" + label + "] " + note)
        return {"seconds": None, "note": note}

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


# What Prometheus says about the same window (the delay from the producer's send to the stored record, 95th percentile). Best effort.
def prometheus_numbers(name, seconds):
    try:
        window = max(15, int(seconds))
        e2e = lib.prom_number("histogram_quantile(0.95, sum by (le) (rate(processor_end_to_end_seconds_bucket[" + str(window) + "s])))")
    except (lib.Fail, ValueError, KeyError):
        e2e = None

    summary_extra.setdefault("prometheus", {})[name] = {"end_to_end_p95_s": None if e2e is None else round(e2e, 2)}


def measure(name, seconds):
    mark(name)
    t0 = now()
    say("\n    measuring '" + name + "' for " + str(seconds) + " s")
    thread = None if (name == "after" and summary_extra.get("skip_after_probe")) else start_probe(name, 90 if name == "before" else 900)
    sleep_for(seconds)
    windows[name] = {"t0": round(t0, 1), "t1": round(now(), 1)}

    if not abort.is_set():
        prometheus_numbers(name, seconds)

    if name == "after":
        return thread

    if thread:
        thread.join(timeout=120)

        if thread.is_alive():
            probes["before"] = {"seconds": None, "note": "the first tagged event did not get through within 120 s"}
            summary_extra["skip_after_probe"] = True

    return None


def read_hpa_settings():
    h = lib.kube_json("-n", lib.NS, "get", "hpa", "producer-load")

    # kubectl apply has returned, so the API server holds the new maximum. A short wait is only a safety net.
    for _ in range(10):
        if h["spec"]["maxReplicas"] > h["spec"].get("minReplicas", 1):
            break

        time.sleep(1)
        h = lib.kube_json("-n", lib.NS, "get", "hpa", "producer-load")

    settings = {"min": h["spec"].get("minReplicas", 1), "max": h["spec"]["maxReplicas"]}

    for metric in h["spec"].get("metrics", []):
        if metric.get("type") == "Resource":
            settings["cpu_target"] = metric["resource"]["target"].get("averageUtilization")

    try:
        p = lib.kube_json("-n", lib.NS, "get", "hpa", "processor")
        settings.update(processor_min=p["spec"].get("minReplicas", 1), processor_max=p["spec"]["maxReplicas"],
                        processor_target=p["spec"]["metrics"][0]["resource"]["target"].get("averageUtilization"))
    except (lib.Fail, KeyError, IndexError):
        pass

    return settings


def ramp():
    mark("ramp")
    began_ramp = now()
    say("\n[3] Letting the autoscaler work (producer/loadtest: the same load, but the autoscaler may add pods). From here Kubernetes decides how many producer pods run.")
    switch("on")
    summary_extra["hpa"] = read_hpa_settings()
    wanted = summary_extra["hpa"]["max"]
    say("    waiting until " + str(wanted) + " producer pods are ready (at most " + str(args.ramp_timeout) + " s)")

    def reached():
        row = latest()

        if row and row["t"] - began_ramp > args.hpa_wait and row["producer_cpu_pct"] is None and row["producer_pods_ready"] <= 1:
            raise lib.Fail("after " + str(args.hpa_wait) + " s the autoscaler still has no CPU number for the producer pods, so it cannot scale. What it said about itself is in " + args.out + "/" + args.prefix + "-hpa.txt (look for ScalingActive and FailedGetResourceMetric)")

        return row and row["producer_pods_ready"] >= wanted

    if wait_until(reached, args.ramp_timeout):
        summary_extra["reached_max_after"] = round(now() - began_ramp, 1)
        say("    all " + str(wanted) + " producer pods are ready " + format(now() - began_ramp, ".0f") + " s after the autoscaler was freed")
    elif not abort.is_set():
        row = latest()
        say("    the time is up with " + str(row["producer_pods_ready"] if row else "?") + " of " + str(wanted) + " producer pods ready: the second window measures what the autoscaler reached")


# Load off: the producers shrink, the backlog goes away, the processors shrink. Each time is measured from the moment the load was switched off.
def shrink(probe_thread):
    mark("shrink")
    off_at = now()
    smallest = summary_extra.get("hpa", {}).get("min", 1)
    smallest_processors = summary_extra.get("hpa", {}).get("processor_min", 1)
    say("\n[4] Switching the load off (producer/load). Watching everything shrink back (the producers at most " + str(args.shrink_timeout) + " s).")
    switch("off")

    if abort.is_set():
        say("    (the test stopped early; the load is off, and the waiting for the pods to shrink is skipped)")
        return

    def producers_down():
        row = latest()
        return row and row["producer_pods_ready"] <= smallest and row["producer_pods_wanted"] <= smallest and (row["producers_msg_s"] or 0) == 0

    if wait_until(producers_down, args.shrink_timeout, interval=2):
        summary_extra["producers_back_after"] = round(now() - off_at, 1)
        say("    the producers are back to " + str(smallest) + " pod, " + format(now() - off_at, ".0f") + " s after the load was switched off")
    elif not abort.is_set():
        say("    the time is up with " + str(latest("producer_pods_ready")) + " producer pods")

    # The backlog: whatever the producers sent that the processors have not stored yet.
    if baseline.get("kafka_end") is None:
        say("    Kafka's own numbers are not available, so the test cannot see the backlog. Check later with:  make logs")
    else:
        mark("drain")
        say("    waiting for the processors to store the backlog (at most " + str(args.drain_timeout) + " s)")

        def caught_up():
            lag = latest("kafka_lag")
            return lag is not None and lag <= 1000 and not (probe_thread and probe_thread.is_alive())

        if wait_until(caught_up, args.drain_timeout, interval=2):
            summary_extra["backlog_gone_after"] = round(now() - off_at, 1)
            say("    the backlog is gone, " + format(now() - off_at, ".0f") + " s after the load was switched off")
        else:
            lag = latest("kafka_lag")
            summary_extra["backlog_left"] = lag
            say("    the time is up; the backlog is " + (format(lag, ",") if lag is not None else "unknown") + " messages. The processors keep working on it.")

    if args.cooldown > 0 and not abort.is_set():
        mark("cooldown")
        say("    watching the processors shrink back (at most " + str(args.cooldown) + " s)")

        def processors_down():
            row = latest()
            return row and row["processor_pods_ready"] <= smallest_processors and row["processor_pods_wanted"] <= smallest_processors

        if wait_until(processors_down, args.cooldown, interval=2):
            summary_extra["processors_back_after"] = round(now() - off_at, 1)
            say("    the processors are back to " + str(smallest_processors) + " pod, " + format(now() - off_at, ".0f") + " s after the load was switched off")
        else:
            say("    the time is up with " + str(latest("processor_pods_ready")) + " processor pods; their autoscaler removes one pod at a time and waits before it starts")


problem_text = None
finished = False
sampler = None
load_left = False         # True when the load may still be on the cluster after the clean-up

try:
    out_dir.mkdir(parents=True, exist_ok=True)
    sys.stdout = Tee(sys.stdout, out_dir / (args.prefix + "-run.txt"))

    for signal_name in ("SIGINT", "SIGHUP", "SIGTERM"):
        signal.signal(getattr(signal, signal_name), stop_on_signal)

    csv_handle = open(out_dir / (args.prefix + "-samples.csv"), "w", newline="")
    csv_writer = csv.DictWriter(csv_handle, fieldnames=report.COLUMNS)
    csv_writer.writeheader()
    hpa_log = open(out_dir / (args.prefix + "-hpa-over-time.txt"), "w")
    hpa_log.write("The output of:  kubectl -n " + lib.NS + " get hpa   taken every " + format(args.hpa_every, ".0f") + " seconds while the scaling test ran.\n"
                  "Times are UTC; '+N s' counts from the start of the test; 'phase' is what the test was doing at that moment.\n"
                  "TARGETS is  current CPU / target CPU,  in percent of what each pod asked for (its CPU request). REPLICAS is the number of pods.\n\n")
    sampler = Sampler(csv_writer, csv_handle)
    started_text = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    print("Scaling test: one producer pod, then the autoscaler, then the load off again (namespace " + lib.NS + ")")
    print("Limits: " + format(args.max_messages, ",") + " messages, " + str(args.max_minutes) + " minutes, any restart of kafka-0 or mongo-0. Ctrl-C stops the test and switches the load off.\n")

    preflight()
    sampler.start()
    mark("warmup")
    load_active = load_started = True
    say("\n[2] Switching the load on, ONE producer pod (producer/loadtest-one: the autoscaler may not add pods).")
    switch("one")

    if not wait_until(lambda: (latest("producer_pods_ready") or 0) >= 1 and (latest("producers_msg_s") or 0) > 0, 180):
        raise lib.Fail("no producer pod is sending after 180 s. Run:  make diagnose")

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

    shrink(after_probe)
    finished = not abort.is_set()
except KeyboardInterrupt:
    ignore_signals()
    problem_text = "stopped by the user (Ctrl-C)" if stopped_by[:1] in ([], ["SIGINT"]) else "stopped by a signal (" + stopped_by[0] + ": the window was closed, or the test was killed)"
    print("\n  Stopped. Switching the load off. This takes about 20 seconds: pressing Ctrl-C again does nothing now.")
except lib.Fail as problem:
    problem_text = str(problem)
except Exception:
    traceback.print_exc()
    problem_text = "the test itself broke (see the error above). Paste it to your assistant."
finally:
    ignore_signals()

    try:
        if load_started:
            try:
                save_hpa_evidence()
            except Exception:
                pass

            if current_phase() not in ("shrink", "drain", "cooldown") or abort.is_set() or problem_text:
                switch_off()

            load_left = check_load_off() is not True
    except Exception as error:
        load_left = True
        print("  Could not switch the load off: " + str(error) + "\n  Run:  make scale-down")

    if sampler is not None:
        sampler.stopping.set()

        if sampler.is_alive():
            sampler.join(timeout=60)

    mark("end")

    if abort_reason:
        problem_text = problem_text or abort_reason[0]

# ---------------------------------------------------------------- the files and the table

try:
    code, text, err = lib.kubectl("-n", lib.NS, "get", "hpa", timeout=30) if load_started else (1, "", "")

    if code == 0 and hpa_log is not None and samples:
        append_hpa(now(), {"phase": "end", "producer_pods_ready": latest("producer_pods_ready"), "processor_pods_ready": latest("processor_pods_ready")}, text)
except Exception:
    pass

for handle in (globals().get("csv_handle"), hpa_log):
    try:
        handle.close()
    except Exception:
        pass

if samples:
    (out_dir / (args.prefix + "-producer-log.txt")).write_text("The lines the load-generating producer pods printed (kubectl logs), in the order the test saw them.\n\n" + "\n".join(raw_log) + "\n")
    summary = {"started": started_text, "args": vars(args), "phases": phases, "windows": windows, "probes": probes, "aborted": problem_text,
               "baseline": baseline, "samples": len(samples)}
    summary.update(summary_extra)
    (out_dir / (args.prefix + "-summary.json")).write_text(json.dumps(summary, indent=2) + "\n")
    print("\n" + report.render(str(out_dir), args.prefix))
    print("Files in " + args.out + "/:  " + ", ".join(args.prefix + s for s in ("-run.txt", "-samples.csv", "-summary.json", "-results.md", "-goals.json", "-chart.svg", "-hpa-over-time.txt", "-hpa.txt", "-producer-log.txt")))
    tally = report.goal_tally(str(out_dir), args.prefix)

    if tally:
        print(tally)

if finished and not problem_text:
    before, after = report.window_numbers(samples, windows["before"]["t0"], windows["before"]["t1"]), report.window_numbers(samples, windows["after"]["t0"], windows["after"]["t1"])
    print("\nRESULT: DONE - producers sent " + report.num(before["producers_msg_s"]) + " msg/s with " + report.pods(before["producer_pods"]) + " pod and "
          + report.num(after["producers_msg_s"]) + " msg/s with " + report.pods(after["producer_pods"]) + " pods; MongoDB stored " + report.num(before["stored_msg_s"]) + " and "
          + report.num(after["stored_msg_s"]) + " msg/s" + ((". The processors were still " + format(summary_extra["backlog_left"], ",") + " messages behind when the test ended; they keep working on it.") if summary_extra.get("backlog_left") else ""))
else:
    print("\nRESULT: STOPPED EARLY - " + " ".join((problem_text or "the test did not finish").split()))

    if load_left:
        print("WARNING: the load may still be running on the cluster. Run:  make scale-down")

    sys.exit(1)

if load_left:
    print("WARNING: the load may still be running on the cluster. Run:  make scale-down")
    sys.exit(1)
