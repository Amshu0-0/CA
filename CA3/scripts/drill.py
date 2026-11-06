# Resilience drill: deletes one pod in every tier of the pipeline, one tier after the other, and records how the platform brings it back.
#
#   make drill                                   all five tiers, about 25 minutes
#   make drill DRILL_ARGS="--only restapi"       one or more tiers (kafka, processor, mongo, restapi, producer), for example for the video
#   make drill DRILL_ARGS="--dry-run"            look and say what would be deleted; delete nothing
#   make drill-report                            draw the table, the goals and the chart again from the saved files (needs no cluster)
#   python3 scripts/drill.py --help              the whole list of options
#
# Everything this program prints is also saved in <prefix>-run.txt (no "| tee" needed; a --dry-run saves nothing, so it can never replace the
# text of a real run). Ctrl-C, closing the Terminal window and "kill" all take the same way out: the load is switched off (when the drill
# switched it on), then the files and the table are written.
#
# For each tier the drill does this:
#   1. Waits until the whole stack is whole (every part has its pods ready) and writes down how things are: the number of records in MongoDB,
#      the number of messages in Kafka, which disk the pod uses.
#   2. Deletes ONE pod with  kubectl delete pod <name>  (that is all it deletes) and starts the clock.
#   3. Sends one tagged event through the pipeline right after the delete (the same event as in make smoke), so there is a test event that
#      travels while the pod is being replaced.
#   4. Watches, every second or two, what Kubernetes does: the old pod Terminating, the new pod Pending, Init, Running, Ready, and the
#      pods of other parts that react (for example the REST API pods when MongoDB is gone). Meanwhile this computer calls the REST API
#      about once a second, so there are numbers for "was the API available".
#   5. Measures: seconds until the new pod is ready, until the whole stack is whole again, until the test event can be read from the REST API.
#   6. Checks that no data is lost: the number of records in MongoDB and of messages in Kafka did not go down, the disk is the same disk,
#      the test event is stored. A tier is PASS only when everything came back and nothing is missing.
#   7. Collects the evidence of that tier: the events Kubernetes wrote about it, the logs of the old and the new pod from Loki, and a few
#      numbers from Prometheus (a lookup that fails is written down as n/a and never stops the drill).
#
# The five tiers (in this order):
#   kafka      the pod kafka-0 (StatefulSet, its messages are on a disk that stays)
#   processor  the processor pod (Deployment; Kafka keeps the events until a new processor reads them)
#   mongo      the pod mongo-0 (StatefulSet, its data is on a disk that stays)
#   restapi    one of the two REST API pods (Deployment; the other one keeps answering)
#   producer   the load generator: the drill switches the load on with ONE producer pod (producer/loadtest-one), deletes that pod, and
#              checks that the new pod sends again; at the end it switches the load off again (producer/load)
#
# Files in evidence/ (the prefix is step7-drill, or step7-drill-<tiers> when --only is used, so a short run never replaces the full one):
#   <prefix>-run.txt        everything this program printed
#   <prefix>-summary.json   the numbers (drillreport.py reads this)
#   <prefix>-timeline.txt   what each pod did, second by second, for every tier
#   <prefix>-events.txt     the events Kubernetes wrote during each tier (Killing, Scheduled, Pulled, Started, Unhealthy ...)
#   <prefix>-rest-calls.csv one line per call that this computer made to the REST API
#   <prefix>-observability.txt  the Loki and Prometheus evidence of each tier
#   <prefix>-results.md, <prefix>-goals.json, <prefix>-chart.svg   the table, the goals of slo.json with their verdicts, the chart
#
# A note on honesty: times have a resolution of about 2 seconds (the pods are looked at every 1.5 seconds, and every look takes a moment),
# and the checks of data compare counts before and after; they cannot see a single changed record. What the drill cannot show is listed in the README.

import argparse
import csv
import json
import os
import re
import signal
import sys
import threading
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

import ca3lib as lib
import drillreport as report

TIERS = ("kafka", "processor", "mongo", "restapi", "producer")
FOLDERS = {"one": "producer/loadtest-one", "off": "producer/load"}
LOAD_SELECTOR = "app.kubernetes.io/name=producer,app.kubernetes.io/component=load-generator"
RATE_LINE = re.compile(r"^\[pod/(\S+?)/producer\] (\{.*\})\s*$")

# What each tier is: the label app.kubernetes.io/name of its pods, whether it is a StatefulSet (the new pod has the same name and the same disk), the disk,
# how long the drill waits for the new pod and then for the whole stack (seconds), and how long after the delete the test event is sent
# (Kafka: after the broker has gone, so the producer has to wait for it).
SPECS = {
    "kafka": {"title": "Kafka (the broker)", "label": "kafka", "stateful": True, "pod": "kafka-0", "disk": "data-kafka-0", "wait_pod": 300, "wait_whole": 240, "event_delay": 10, "event_timeout": 420,
              "story": "delete the pod kafka-0. The messages are on a disk that stays, so they must all still be there."},
    "processor": {"title": "The processor", "label": "processor", "stateful": False, "wait_pod": 180, "wait_whole": 180, "event_delay": 2, "event_timeout": 240,
                  "story": "delete the processor pod. Kafka keeps the events until a new processor reads them."},
    "mongo": {"title": "MongoDB (the database)", "label": "mongo", "stateful": True, "pod": "mongo-0", "disk": "data-mongo-0", "wait_pod": 300, "wait_whole": 300, "event_delay": 2, "event_timeout": 240,
              "story": "delete the pod mongo-0. The data is on a disk that stays, so every record must still be there."},
    "restapi": {"title": "The REST API", "label": "restapi", "stateful": False, "wait_pod": 150, "wait_whole": 150, "event_delay": 2, "event_timeout": 240,
                "story": "delete one of the two REST API pods. The other one must keep answering."},
    "producer": {"title": "The producer", "label": "producer", "role": "load-generator", "stateful": False, "wait_pod": 150, "wait_whole": 150, "event_delay": 2, "event_timeout": 240,
                 "story": "switch the load on with one producer pod, then delete that pod. The Deployment must start a new one that sends again."},
}

# The parts of the stack and the least number of ready pods each must have for the stack to count as whole. The log collector (alloy) runs on every node,
# so its number is whatever it was before the drill.
MINIMUM = {"kafka": 1, "mongo": 1, "processor": 1, "restapi": 2, "producer": 1, "prometheus": 1, "grafana": 1, "loki": 1, "kafka-exporter": 1, "mongodb-exporter": 1}
PER_NODE = ("alloy",)

parser = argparse.ArgumentParser(description="Delete one pod in each tier of the pipeline and measure how the platform recovers.")
parser.add_argument("--only", help="comma-separated tiers to drill: " + ", ".join(TIERS) + " (default: all five, in this order)")
parser.add_argument("--dry-run", action="store_true", help="look at the stack and say which pods would be deleted; delete nothing")
parser.add_argument("--pause", type=int, default=30, help="seconds of rest after each tier, so the graphs in Grafana show the recovery (default 30)")
parser.add_argument("--settle", type=int, default=12, help="seconds to wait after a tier before its logs and metrics are collected (default 12)")
parser.add_argument("--producer-seconds", type=int, default=25, help="seconds the load runs before the producer pod is deleted (default 25)")
parser.add_argument("--max-minutes", type=int, default=60, help="stop the drill after this many minutes (default 60)")
parser.add_argument("--no-evidence", action="store_true", help="do not collect the Kubernetes events, the Loki logs and the Prometheus numbers (faster)")
parser.add_argument("--out", default="evidence", help="folder for the result files (default: evidence)")
parser.add_argument("--prefix", help="start of the result file names (default: step7-drill, or step7-drill-<tiers> when --only is used)")
parser.add_argument("--host", help="address of the node to call for the REST API (default: the server, from Terraform)")
parser.add_argument("--port", type=int, help="node port (default: rest_node_port in infra/config.yml)")
args = parser.parse_args()

if args.only and not set(args.only.split(",")) <= set(TIERS):
    sys.exit("--only takes a comma-separated list of: " + ", ".join(TIERS))

chosen = [t for t in TIERS if not args.only or t in args.only.split(",")]
prefix = args.prefix or ("step7-drill" if not args.only else "step7-drill-" + "-".join(chosen))
out_dir = Path(args.out) if Path(args.out).is_absolute() else lib.ROOT / args.out

began = time.monotonic()
epoch = (datetime.now(timezone.utc), began)
lock = threading.RLock()
done = threading.Event()         # the helper threads stop when this is set
current = {"tier": None, "t0": None}   # the tier being drilled and the moment (time.monotonic()) of its delete; t0 is None between tiers
results = []                     # one dict per tier, in the order of the drill
windows = []                     # {"tier", "from_local", "to_local"}: the time range to set in Grafana
timeline_blocks, events_blocks, observability_blocks = [], [], []
host = port = topic = group = None
watcher = poller = None
monitoring = {"prometheus": False, "loki": False}
load_switched_on = False         # True while the drill itself has switched the load generator on
stopped_by = []


def wall(moment):
    return epoch[0] + timedelta(seconds=moment - epoch[1])


def clock(moment):
    return wall(moment).strftime("%H:%M:%S")


def local_clock(moment):
    return wall(moment).astimezone().strftime("%H:%M")


# Everything the drill prints also goes into <prefix>-run.txt, so nobody needs "| tee": a pipe would die on Ctrl-C and cut off the clean-up.
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


# A line of the drill with the seconds since the delete in front: "   +12 s   kafka-0: Terminating".
def at(text, moment=None):
    t0 = current["t0"]
    seconds = (moment if moment is not None else time.monotonic()) - t0 if t0 is not None else 0.0
    say(("+" + format(seconds, ".0f") + " s").rjust(10) + "   " + text)


def pause_for(seconds):
    end = time.monotonic() + seconds

    while time.monotonic() < end:
        time.sleep(max(0.05, min(0.5, end - time.monotonic())))


def number(value):
    return "n/a" if value is None else format(value, ",")


# "+3 s" or "-2 s": the seconds relative to the delete, never "-0 s".
def offset(seconds):
    whole = int(round(seconds))
    return ("-" if whole < 0 else "+") + str(abs(whole)) + " s"


# Text padded to a width, with at least one space after it (a name that is longer than the column must not run into the next column).
def column(text, width):
    return (str(text) + " ").ljust(width)


def plural(count, word):
    return str(count) + " " + word + ("" if count == 1 else "s")


# ---------------------------------------------------------------- what a pod is doing, in the words of  kubectl get pods

# The STATUS column of  kubectl get pods , worked out from the JSON the same way kubectl does it.
def pod_state(p):
    meta, status = p["metadata"], p.get("status", {})
    reason = status.get("reason") or status.get("phase") or "Unknown"
    initial = status.get("initContainerStatuses") or []
    initializing = False

    for i, c in enumerate(initial):
        state = c.get("state", {})
        ended = state.get("terminated")

        if ended and ended.get("exitCode") == 0:
            continue

        initializing = True

        if ended:
            reason = "Init:" + (ended.get("reason") or ("Signal:" + str(ended["signal"]) if ended.get("signal") else "ExitCode:" + str(ended.get("exitCode"))))
        elif state.get("waiting") and state["waiting"].get("reason") not in (None, "", "PodInitializing"):
            reason = "Init:" + state["waiting"]["reason"]
        else:
            reason = "Init:" + str(i) + "/" + str(len(p.get("spec", {}).get("initContainers", [])))

        break

    if not initializing:
        for c in reversed(status.get("containerStatuses") or []):
            state = c.get("state", {})

            if state.get("waiting") and state["waiting"].get("reason"):
                reason = state["waiting"]["reason"]
            elif state.get("terminated"):
                ended = state["terminated"]
                reason = ended.get("reason") or ("Signal:" + str(ended["signal"]) if ended.get("signal") else "ExitCode:" + str(ended.get("exitCode")))

    if meta.get("deletionTimestamp"):
        reason = "Terminating"

    return reason


def pod_info(p):
    meta, status = p["metadata"], p.get("status", {})
    containers = status.get("containerStatuses") or []
    owners = meta.get("ownerReferences") or []
    labels = meta.get("labels") or {}

    return {"name": meta["name"], "uid": meta.get("uid", ""), "state": pod_state(p), "ready": lib.pod_is_ready(p), "terminating": bool(meta.get("deletionTimestamp")),
            "restarts": sum(c.get("restartCount", 0) for c in containers), "component": labels.get("app.kubernetes.io/name", ""), "role": labels.get("app.kubernetes.io/component", ""),
            "job": any(o.get("kind") == "Job" for o in owners) or "job-name" in labels or "batch.kubernetes.io/job-name" in labels, "node": p.get("spec", {}).get("nodeName", "")}


# The words the drill uses for a pod: "Running, ready", "Running, not ready", "Init:0/1", "Terminating", "CrashLoopBackOff" ... and the restarts, when there are some.
def describe(info):
    text = info["state"]

    if info["state"] == "Running":
        text += ", ready" if info["ready"] else ", not ready"

    if info["restarts"]:
        text += " (restarts: " + str(info["restarts"]) + ")"

    return text


# ---------------------------------------------------------------- the helpers that watch while a tier runs

# Looks at all pods every 1.5 seconds. Remembers every look, and every change of a pod, and prints the changes while a tier is running.
class PodWatcher(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.latest, self.latest_at, self.previous, self.first = {}, None, {}, True
        self.history = []        # (time, {name: info}) for every look
        self.changes = []        # (time, pod name, text)
        self.failures = 0

    def run(self):
        while not done.is_set():
            started = time.monotonic()

            try:
                items = lib.kube_json("-n", lib.NS, "get", "pods", timeout=30)["items"]
            except lib.Fail:
                self.failures += 1
                done.wait(1.0)
                continue

            looked = time.monotonic()
            snapshot = {}

            for p in items:
                info = pod_info(p)
                snapshot[info["name"]] = info

            with lock:
                self.latest, self.latest_at = snapshot, looked
                self.history.append((looked, snapshot))

            self.compare(looked, snapshot)
            done.wait(max(0.2, 1.5 - (time.monotonic() - started)))

    def compare(self, looked, snapshot):
        notes = []

        for name, info in snapshot.items():
            old = self.previous.get(name)

            if old is None:
                if not self.first:
                    notes.append((name, describe(info) + "   (new pod)"))
            elif describe(old) != describe(info) or old["uid"] != info["uid"]:
                notes.append((name, describe(info)))

        for name in self.previous:
            if name not in snapshot:
                notes.append((name, "gone"))

        self.previous, self.first = snapshot, False

        for name, text in notes:
            with lock:
                self.changes.append((looked, name, text))

            if current["t0"] is not None and looked >= current["t0"] - 1.0:
                at(name + ": " + text, looked)


# Calls the REST API from this computer about once a second: /health (no login; is a REST pod reachable) and /stats (login; needs MongoDB, so it shows
# whether the API can read data). Every call is remembered, with the number of records that /stats reported.
class RestPoller(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.samples = []        # {"t", "health", "health_ms", "stats", "stats_ms", "docs"}; a status of None means nobody answered

    def run(self):
        while not done.is_set():
            started = time.monotonic()
            sample = {"t": started, "health": None, "health_ms": None, "stats": None, "stats_ms": None, "docs": None}

            try:
                status, body = lib.rest_get(host, port, "/health", timeout=2, login=False)
                sample["health"], sample["health_ms"] = status, (time.monotonic() - started) * 1000.0
                second = time.monotonic()
                status, body = lib.rest_get(host, port, "/stats", timeout=3)
                sample["stats"], sample["stats_ms"] = status, (time.monotonic() - second) * 1000.0

                if status == 200:
                    try:
                        sample["docs"] = int(json.loads(body)["documents"])
                    except (ValueError, KeyError, TypeError):
                        pass
            except lib.Fail:
                pass

            with lock:
                self.samples.append(sample)

            done.wait(max(0.1, 1.0 - (time.monotonic() - started)))

    # (calls, failed, longest stretch of failures in seconds, when that stretch began as seconds after t0) for one kind of call between two moments.
    def numbers(self, key, t_from, t_to):
        with lock:
            rows = [s for s in self.samples if t_from <= s["t"] <= t_to]

        failed = [s for s in rows if s[key] != 200]
        longest, start_of_longest, since = 0.0, None, None

        for s in rows:
            if s[key] != 200:
                since = s["t"] if since is None else since
                length = s["t"] - since + 1.0       # the call that failed stands for about a second

                if length > longest:
                    longest, start_of_longest = length, since
            else:
                since = None

        return len(rows), len(failed), longest, (start_of_longest - t_from) if start_of_longest is not None else None


# Sends one tagged event (the smoke test's event) a few seconds after the delete, in a thread of its own, because it may have to wait for Kafka.
class EventSender(threading.Thread):
    def __init__(self, delay, timeout):
        super().__init__(daemon=True)
        self.trace, self.delay, self.timeout = lib.new_trace_id(), delay, timeout
        self.sent, self.sent_at, self.error, self.finished, self.started_at = None, None, None, False, None

    def run(self):
        if done.wait(self.delay):
            self.finished = True
            return

        self.started_at = time.monotonic()

        try:
            self.sent = lib.send_event(self.trace, timeout=self.timeout)
            self.sent_at = time.monotonic()
        except lib.Fail as problem:
            self.error = " ".join(str(problem).split())[:300]
        except Exception as problem:
            self.error = problem.__class__.__name__ + ": " + str(problem)[:200]
        finally:
            self.finished = True


# ---------------------------------------------------------------- small questions to the cluster

def ready_counts(snapshot):
    counts = {}

    for info in snapshot.values():
        if info["job"] or info["terminating"] or not info["ready"]:
            continue

        counts[info["component"]] = counts.get(info["component"], 0) + 1

    return counts


# How many ready pods each part of the stack needs for the stack to be whole: the fixed numbers of MINIMUM, and for the log collector the number it had.
def needs_of(snapshot):
    need = dict(MINIMUM)
    counts = ready_counts(snapshot)

    for name in PER_NODE:
        if counts.get(name):
            need[name] = counts[name]

    return need


NEED = {}      # the least number of ready pods of each part for the stack to count as whole; fixed at the start (preflight)
CORE = ("kafka", "mongo", "processor", "restapi", "producer")


def missing_parts(snapshot, need):
    counts = ready_counts(snapshot)
    return [name + " (" + str(counts.get(name, 0)) + " of " + str(wanted) + " ready)" for name, wanted in need.items() if counts.get(name, 0) < wanted]


def disk(claim):
    found = lib.kube_json("-n", lib.NS, "get", "pvc", claim)
    return found["spec"].get("volumeName", "?")


# The number of records in MongoDB, asked inside the MongoDB pod. Exact up to 200,000 records; above that MongoDB's own estimate, which is exact after a clean stop.
def docs():
    database, collection = lib.config_value("pipeline-config", "MONGO_DB"), lib.config_value("pipeline-config", "MONGO_COLLECTION")
    script = ('const c = db.getSiblingDB("' + database + '").getCollection("' + collection + '"); const est = Number(c.estimatedDocumentCount()); '
              'print(JSON.stringify({est: est, exact: est < 200000 ? Number(c.countDocuments({})) : null}))')
    answer = lib.mongosh_json(script, timeout=120)

    return answer["exact"] if answer.get("exact") is not None else answer["est"]


def kafka_total():
    ends = lib.kafka_end_offsets(topic)
    return sum(ends.values()) if ends else None


def lag():
    rows = lib.kafka_group_lag(group)
    return sum(r[3] or 0 for r in rows) if rows else None


def event_status(trace):
    return lib.rest_get(host, port, "/events/" + trace, timeout=4)[0]


# The sending rates the load generator's pods print (one JSON line every 5 seconds): {pod name: messages per second}, from the last lines of their logs.
def producer_rates():
    code, out, err = lib.kubectl("-n", lib.NS, "logs", "-l", LOAD_SELECTOR, "-c", "producer", "--prefix", "--tail=3", timeout=40)
    rates = {}

    if code != 0:
        return rates

    for line in out.splitlines():
        found = RATE_LINE.match(line.strip())

        if not found:
            continue

        try:
            record = json.loads(found.group(2))
        except ValueError:
            continue

        if record.get("msg") == "rate":
            rates[found.group(1)] = record.get("rate_msg_s")

    return rates


# ---------------------------------------------------------------- the load generator (producer tier only)

def switch(which):
    code, out, err = lib.kubectl("apply", "-k", str(lib.ROOT / FOLDERS[which]), timeout=90)

    if code != 0:
        raise lib.Fail("could not switch the load (" + FOLDERS[which] + "):\n" + err.strip())


# Is the load generator sending? (True when it is idle, the autoscaler's maximum) or None when the cluster does not answer.
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
    global load_switched_on
    load_switched_on = False

    for attempt in range(3):
        try:
            switch("off")
            return True
        except lib.Fail as problem:
            if attempt < 2:
                say("  (switching the load off did not work: " + [line for line in str(problem).splitlines() if line.strip()][-1].strip()[:100] + "; trying again)")
                time.sleep(3)

    return False


# After the clean-up, ask the cluster once more, so that nobody believes the load is off while it is still sending.
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


# ---------------------------------------------------------------- one tier

def pick_victim(spec):
    with lock:
        snapshot = dict(watcher.latest)

    if spec["stateful"]:
        info = snapshot.get(spec["pod"])

        if info is None or not info["ready"]:
            raise lib.Fail(spec["pod"] + " is not ready")

        return info

    pods = sorted((i for i in snapshot.values() if i["component"] == spec["label"] and i["ready"] and not i["terminating"] and not i["job"] and i["role"] == spec.get("role", i["role"])),
                  key=lambda i: i["name"])

    if not pods:
        raise lib.Fail("there is no ready " + spec["label"] + " pod to delete")

    return pods[0]


def find_replacement(snapshot, spec, victim, base_names):
    for info in snapshot.values():
        if info["terminating"] or info["job"]:
            continue

        if spec["stateful"]:
            if info["name"] == victim["name"] and info["uid"] != victim["uid"]:
                return info
        elif info["component"] == spec["label"] and info["name"] not in base_names and info["role"] == spec.get("role", info["role"]):
            return info

    return None


# Wait (up to the time given) until the whole stack is whole. Returns the list of what is still missing (empty = whole).
def wait_whole(need, timeout):
    end = time.monotonic() + timeout
    missing = ["?"]

    while time.monotonic() < end:
        with lock:
            snapshot = dict(watcher.latest)

        missing = missing_parts(snapshot, need)

        if not missing:
            problems = lib.stack_problems()

            if not problems:
                return []

            missing = problems

        pause_for(3)

    return missing


def start_load():
    global load_switched_on
    say("      switching the load on with ONE producer pod (kubectl apply -k producer/loadtest-one) ...")
    load_switched_on = True
    switch("one")
    end = time.monotonic() + 180
    seen = None

    while time.monotonic() < end:
        rates = producer_rates()
        sending = {n: r for n, r in rates.items() if r}

        if sending:
            seen = sending
            break

        pause_for(4)

    if not seen:
        raise lib.Fail("no producer pod is sending after 180 s. Run:  make diagnose")

    def one_pod():
        with lock:
            return len([i for i in watcher.latest.values() if i["component"] == "producer" and i["role"] == "load-generator" and not i["terminating"]]) == 1

    lib.wait_for(one_pod, 90, interval=2)
    say("      the producer is sending (" + ", ".join(format(r, ",") + " messages per second" for r in seen.values()) + "); letting it run for " + str(args.producer_seconds) + " seconds first")
    pause_for(args.producer_seconds)


def drill_tier(tier, number_, total):
    global load_switched_on
    spec = SPECS[tier]
    r = {"tier": tier, "title": spec["title"], "deleted": False, "victim": None, "kind": "StatefulSet" if spec["stateful"] else "Deployment", "replacement": None,
         "delete_wall": None, "old_gone_s": None, "pod_back_s": None, "whole_s": None, "sending_again_s": None,
         "event": {"trace": None, "sent_s": None, "readable_s": None, "copies": None, "error": None},
         "rest": {}, "data": {"checks": [], "kept": None}, "restarts": {}, "problem": None, "passed": False}
    results.append(r)
    say("\n[" + str(number_) + "/" + str(total) + "] " + spec["title"] + ": " + spec["story"])
    sender = None
    t0 = None

    try:
        say("      checking that the whole stack is whole before the delete ...")
        missing = wait_whole(NEED, 240)

        if missing:
            raise lib.Fail("the stack is not whole, so the drill would measure the wrong thing: " + ", ".join(missing) + ". Run:  make status")

        if tier == "producer":
            start_load()

        with lock:
            snapshot = dict(watcher.latest)

        base_names = set(snapshot)
        base_restarts = {n: i["restarts"] for n, i in snapshot.items()}
        victim = pick_victim(spec)

        if tier == "restapi" and ready_counts(snapshot).get("restapi", 0) < 2:
            raise lib.Fail("this tier needs 2 ready REST API pods")

        before = {"docs": docs()}
        parts = [number(before["docs"]) + " records in MongoDB"]

        if tier == "kafka":
            before["kafka"] = kafka_total()
            parts.append(number(before["kafka"]) + " messages in Kafka")

        if spec.get("disk"):
            before["disk"] = disk(spec["disk"])
            parts.append("the disk of " + victim["name"] + " is " + before["disk"])

        say("      before: " + ", ".join(parts))
        say("      deleting " + victim["name"] + " now. From here on, only the platform acts: this program just watches.\n")

        sender = EventSender(spec["event_delay"], spec["event_timeout"])
        r["event"]["trace"] = sender.trace
        t0 = time.monotonic()
        current["tier"], current["t0"] = tier, t0
        code, out, err = lib.kubectl("-n", lib.NS, "delete", "pod", victim["name"], "--wait=false", timeout=60)

        if code != 0:
            current["t0"] = None
            raise lib.Fail("kubectl delete pod " + victim["name"] + " failed: " + (err.strip() or out.strip() or "no message"))

        r["deleted"], r["victim"], r["delete_wall"] = True, victim["name"], wall(t0).strftime("%Y-%m-%dT%H:%M:%SZ")
        at("kubectl delete pod " + victim["name"] + "   ->   " + out.strip().splitlines()[0] if out.strip() else "kubectl delete pod " + victim["name"], t0)
        sender.start()
        final = t0 + spec["wait_pod"] + spec["wait_whole"]
        polled = {"event": 0.0, "whole": 0.0, "rates": 0.0}      # when each of the slower questions was last asked
        sent_said = sending_said = False

        while time.monotonic() < final:
            with lock:
                snapshot, looked = dict(watcher.latest), watcher.latest_at

            if r["old_gone_s"] is None and not any(i["uid"] == victim["uid"] for i in snapshot.values()):
                r["old_gone_s"] = looked - t0

            if r["pod_back_s"] is None:
                found = find_replacement(snapshot, spec, victim, base_names)

                if found and found["ready"]:
                    r["pod_back_s"], r["replacement"] = looked - t0, found["name"]
                    at("the new pod " + found["name"] + " is ready   <-- the platform replaced the pod", looked)

            if r["pod_back_s"] is None and looked - t0 > spec["wait_pod"]:
                break       # no replacement is ready after the time limit: waiting longer proves nothing

            if tier == "producer" and r["pod_back_s"] is not None and r["sending_again_s"] is None and time.monotonic() - polled["rates"] >= 4.0:
                polled["rates"] = time.monotonic()
                rates = {n: v for n, v in producer_rates().items() if v and n not in base_names}

                if rates:
                    r["sending_again_s"] = time.monotonic() - t0

                    if not sending_said:
                        at("the new producer pod sends again (" + format(list(rates.values())[0], ",") + " messages per second)")
                        sending_said = True

            if sender.sent_at is not None and r["event"]["sent_s"] is None:
                r["event"]["sent_s"] = sender.sent_at - t0
                at("the producer Job for the test event " + sender.trace + " has finished: Kafka stored it (partition " + str(sender.sent["partition"]) + ", offset " + str(sender.sent["offset"]) + ")", sender.sent_at)
                sent_said = True

            if sender.error and not sent_said:
                at("the test event could not be sent: " + sender.error)
                sent_said = True

            if sender.sent_at is not None and r["event"]["readable_s"] is None and time.monotonic() - polled["event"] >= 2.0:
                polled["event"] = time.monotonic()

                if event_status(sender.trace) == 200:
                    r["event"]["readable_s"] = time.monotonic() - t0
                    at("the REST API returns the test event (HTTP 200)")

            sending_ok = tier != "producer" or r["sending_again_s"] is not None

            if r["pod_back_s"] is not None and r["whole_s"] is None and sending_ok and not missing_parts(snapshot, NEED) and time.monotonic() - polled["whole"] >= 3.0:
                polled["whole"] = time.monotonic()

                if not lib.stack_problems():
                    r["whole_s"] = time.monotonic() - t0
                    at("the whole stack is whole again (every part has its pods ready)")

            if r["whole_s"] is not None and (r["event"]["readable_s"] is not None or (sender.finished and sender.sent_at is None)) and r["old_gone_s"] is not None:
                break

            pause_for(0.5)

        t_end = time.monotonic()

        if r["pod_back_s"] is None:
            r["problem"] = "the new " + tier + " pod was not ready within " + str(spec["wait_pod"]) + " s. Run:  make diagnose"
        elif r["whole_s"] is None:
            with lock:
                snapshot = dict(watcher.latest)

            r["problem"] = "the new pod is ready, but the stack was not whole within " + str(spec["wait_pod"] + spec["wait_whole"]) + " s: " + ", ".join(missing_parts(snapshot, NEED) or ["see make status"])
        elif sender.error:
            r["problem"] = "the test event could not be sent: " + sender.error
        elif r["event"]["readable_s"] is None:
            r["problem"] = "the test event " + sender.trace + " was not readable from the REST API within the time limit. Run:  make logs-trace"

        finish_checks(tier, spec, r, before, sender, victim, base_restarts, t0, t_end)
    except lib.Fail as problem:
        r["problem"] = r["problem"] or " ".join(str(problem).split())
        say("      !!! " + r["problem"])
    except KeyboardInterrupt:
        r["problem"] = r["problem"] or "stopped before this tier was finished"
        raise
    except Exception:
        traceback.print_exc()
        r["problem"] = r["problem"] or "the drill itself broke (see the error above)"
    finally:
        if t0 is not None and r["deleted"]:
            r["_t0"], r["_t_end"] = t0, time.monotonic()

        current["t0"] = None
        current["tier"] = None

    r["passed"] = bool(r["deleted"] and r["pod_back_s"] is not None and r["whole_s"] is not None and r["event"]["readable_s"] is not None and r["data"]["kept"] is not False and not r["problem"])
    return r


# After the recovery: the checks of the data, the numbers of the REST API, the restarts, and the verdict of the tier.
def finish_checks(tier, spec, r, before, sender, victim, base_restarts, t0, t_end):
    checks = r["data"]["checks"]

    def check(what, was, now, ok, note=""):
        checks.append({"what": what, "before": was, "after": now, "ok": ok, "note": note})

    try:
        after_docs = docs()
    except lib.Fail as problem:
        after_docs = None
        check("records in MongoDB", number(before["docs"]), "not readable", None, " ".join(str(problem).split())[:120])

    copies = None

    if sender.sent_at is not None:
        try:
            found = lib.mongo_lookup(sender.trace)
            copies = int(found["found"])
        except lib.Fail:
            copies = None

    r["event"]["copies"] = copies

    if after_docs is not None:
        extra = after_docs - before["docs"] - (copies or 0)
        note = ""

        if tier == "producer":
            note = "the load adds records all the time, so only 'not fewer than before' is checked"
        elif extra > 0:
            note = str(extra) + " more than the test event explains: the events that Kafka delivered twice, or other traffic"

        check("records in MongoDB", number(before["docs"]), number(after_docs), after_docs >= before["docs"], note)

    if sender.sent_at is not None:
        check("the test event is stored in MongoDB", "sent", str(copies) + " time" + ("" if copies == 1 else "s") if copies is not None else "not readable", None if copies is None else copies >= 1,
              "" if copies in (None, 1) else ("stored more than once: Kafka delivers at least once, so a copy is possible after a restart" if copies > 1 else "the event is missing"))

    if tier == "kafka":
        after_kafka = lib.wait_for(kafka_total, 90, interval=3)[0]
        check("messages in Kafka", number(before.get("kafka")), number(after_kafka), None if before.get("kafka") is None or after_kafka is None else after_kafka >= before["kafka"])

    if spec.get("disk"):
        try:
            again = disk(spec["disk"])
        except lib.Fail:
            again = "not readable"

        check("the disk of " + victim["name"], before["disk"], again, again == before["disk"], "" if again == before["disk"] else "the new pod got another disk: the data did not follow")

    if tier == "processor":
        waiting = lib.wait_for(lambda: lag() == 0, 90, interval=3)[0]
        check("messages waiting in Kafka for the processor", "-", "0" if waiting else number(lag()), True,
              "" if waiting else "the processor was still catching up when the drill looked")

    verdicts = [c["ok"] for c in checks]
    r["data"]["kept"] = False if any(v is False for v in verdicts) else (None if any(v is None for v in verdicts) or not verdicts else True)
    calls, h_failed, h_gap, h_start = poller.numbers("health", t0, t_end)
    calls, s_failed, s_gap, s_start = poller.numbers("stats", t0, t_end)
    r["rest"] = {"calls": calls, "health_failed": h_failed, "health_gap_s": round(h_gap, 1), "stats_failed": s_failed, "stats_gap_s": round(s_gap, 1),
                 "stats_gap_start_s": round(s_start, 1) if s_start is not None else None}

    with lock:
        snapshot = dict(watcher.latest)

    r["restarts"] = {n: i["restarts"] - base_restarts.get(n, 0) for n, i in snapshot.items() if i["restarts"] - base_restarts.get(n, 0) > 0 and not i["job"]}

    if r["restarts"]:
        at("Kubernetes restarted by itself: " + ", ".join(n + " (+" + str(v) + ")" for n, v in sorted(r["restarts"].items())))

    at("REST API asked " + str(calls) + " times from this computer: /health failed " + str(h_failed) + ", /stats failed " + str(s_failed) + (" (longest stretch " + format(s_gap, ".0f") + " s)" if s_failed else ""))

    for c in checks:
        at(("ok:       " if c["ok"] is True else ("PROBLEM:  " if c["ok"] is False else "unknown:  ")) + c["what"] + ": " + str(c["before"]) + " -> " + str(c["after"]) + ((" (" + c["note"] + ")") if c["note"] else ""))


# ---------------------------------------------------------------- the evidence of a tier: Kubernetes events, Loki, Prometheus

def parse_time(stamp):
    if not stamp:
        return None

    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


KEY_REASONS = ("Killing", "Scheduled", "SuccessfulCreate", "SuccessfulDelete", "Unhealthy", "BackOff", "Failed", "FailedScheduling", "FailedMount", "Created", "Started")


# The events Kubernetes wrote between two moments: [(time, type, reason, object, message, count)], or (None, why).
def kube_events(t_from, t_to):
    code, out, err = lib.kubectl("-n", lib.NS, "get", "events", "-o", "json", timeout=60)

    if code != 0:
        return None, err.strip() or "kubectl failed"

    rows = []

    for e in json.loads(out).get("items", []):
        when = parse_time(e.get("lastTimestamp") or e.get("eventTime") or e.get("firstTimestamp") or e.get("metadata", {}).get("creationTimestamp"))

        if when is None or when < t_from or when > t_to:
            continue

        obj = e.get("involvedObject", {})
        rows.append((when, e.get("type", ""), e.get("reason", ""), obj.get("kind", "") + "/" + obj.get("name", ""), " ".join((e.get("message") or "").split())[:200], e.get("count") or 1))

    return sorted(rows), None


def short_line(line, size=220):
    line = " ".join(line.split())
    return line if len(line) <= size else line[:size - 3] + "..."


def collect_evidence(r, t0, t_end):
    tier = r["tier"]
    seconds = int(t_end - t0) + int(args.settle) + 60
    minutes = max(5, seconds // 60 + 2)
    t_from, t_to = wall(t0) - timedelta(seconds=10), wall(time.monotonic()) + timedelta(seconds=5)
    say("      collecting the evidence of this tier (Kubernetes events, Loki, Prometheus) ...")

    # What Kubernetes wrote down.
    try:
        rows, why = kube_events(t_from, t_to)
    except Exception as problem:
        rows, why = None, str(problem)

    block = ["=== " + report.name_of(tier) + ": events of Kubernetes from " + clock(t0 - 10) + " UTC (+ N s is counted from the delete at " + clock(t0) + " UTC)", ""]

    if rows is None:
        block.append("(could not read the events: " + str(why) + ")")
        say("      Kubernetes events: not available (" + str(why)[:80] + ")")
    else:
        for when, kind, reason, obj, message, count in rows:
            block.append(offset((when - wall(t0)).total_seconds()).rjust(8) + "  " + column(kind, 9) + column(reason, 21) + column(obj, 35) + message + (" (x" + str(count) + ")" if count > 1 else ""))

        shown = [x for x in rows if x[2] in ("Killing", "Scheduled", "SuccessfulCreate", "Unhealthy", "BackOff", "Failed", "FailedScheduling")]
        say("      Kubernetes wrote " + plural(len(rows), "event") + " in this time (all in " + prefix + "-events.txt); the ones that matter most:")

        for when, kind, reason, obj, message, count in shown[:10]:
            say("        " + offset((when - wall(t0)).total_seconds()).rjust(7) + "  " + column(reason, 18) + column(obj, 31) + message[:70])

        if not shown:
            say("        (none of the kinds Killing / Scheduled / SuccessfulCreate / Unhealthy)")

    events_blocks.append("\n".join(block) + "\n")

    # Loki and Prometheus.
    lines = ["=== " + report.name_of(tier) + ": logs from Loki and numbers from Prometheus (window: " + str(minutes) + " minutes back from " + clock(time.monotonic()) + " UTC)", ""]

    if not monitoring["loki"]:
        lines.append("Loki: not asked (it did not answer before the drill)")
    else:
        looks = [("the pod that was deleted", r["victim"])]

        if r.get("replacement") and r["replacement"] != r["victim"]:
            looks.append(("the new pod", r["replacement"]))

        for label, pod in looks:
            try:
                found = lib.loki_lines('{namespace="' + lib.NS + '", pod="' + pod + '"}', minutes=minutes, limit=500)
                lines.append("Loki: " + label + " (" + pod + ") has " + plural(len(found), "log line") + " in the window" + (", the first and the last two:" if found else ""))

                for stamp, labels, text in (found[:1] + found[-2:]) if len(found) > 3 else found:
                    lines.append("    " + datetime.fromtimestamp(stamp / 1e9, timezone.utc).strftime("%H:%M:%S") + " " + short_line(text))

                if found:
                    say("      Loki: " + label + " (" + pod + "): " + plural(len(found), "log line") + "; last: " + short_line(found[-1][2], 100))
                else:
                    say("      Loki: " + label + " (" + pod + "): no log lines found (it may not have written any, or the log collector was not yet ready)")
            except lib.Fail as problem:
                lines.append("Loki: " + label + " (" + pod + "): n/a (" + short_line(str(problem), 160) + ")")

        try:
            found = lib.loki_lines('{namespace="' + lib.NS + '"} |~ "(?i)(error|exception|traceback|refused|timed out)"', minutes=minutes, limit=500)
            per_pod = {}

            for stamp, labels, text in found:
                per_pod.setdefault(labels.get("pod", "?"), []).append(text)

            lines.append("")
            lines.append("Loki: the search for  error  across all pods in the window found " + str(len(found)) + " lines in " + str(len(per_pod)) + " pods (this is the search for the screenshot of Step 5):")

            for pod, texts in sorted(per_pod.items()):
                lines.append("    " + pod + ": " + str(len(texts)) + " lines; for example: " + short_line(texts[-1], 160))

            say("      Loki: the search for 'error' across all pods found " + str(len(found)) + " lines in " + str(len(per_pod)) + " pods" + (": " + ", ".join(sorted(per_pod)[:4]) if per_pod else ""))
        except lib.Fail as problem:
            lines.append("Loki: the search for error: n/a (" + short_line(str(problem), 160) + ")")

    lines.append("")

    if not monitoring["prometheus"]:
        lines.append("Prometheus: not asked (it did not answer before the drill)")
    else:
        window = str(seconds) + "s"
        asks = [("the most messages waiting for the processor (kafka_consumergroup_lag)", "max_over_time(sum(kafka_consumergroup_lag{group=\"processor\"})[" + window + ":5s])", ""),
                ("the fewest scrape targets that answered for the processor (up)", "min_over_time(sum(up{job=\"processor\"})[" + window + ":5s])", ""),
                ("the fewest scrape targets that answered for the REST API (up)", "min_over_time(sum(up{job=\"restapi\"})[" + window + ":5s])", "")]

        if tier == "mongo":
            asks.append(("the lowest value of mongodb_up (1 = the exporter reaches MongoDB, 0 = it does not)", "min_over_time(mongodb_up[" + window + ":5s])", ""))

        if tier == "kafka":
            asks.append(("the fewest Kafka brokers the exporter saw (kafka_brokers)", "min_over_time(kafka_brokers[" + window + ":5s])", ""))

        if tier == "producer":
            asks.append(("the highest sending rate of the producers (messages per second)", "max_over_time(sum(rate(producer_messages_sent_total[15s]))[" + window + ":5s])", ""))

        for label, expression, unit in asks:
            try:
                value = lib.prom_number(expression)
                lines.append("Prometheus: " + label + ": " + ("n/a (no samples: the series did not exist for part of the time)" if value is None else format(value, ",.2f").rstrip("0").rstrip(".")) + unit)
            except lib.Fail as problem:
                lines.append("Prometheus: " + label + ": n/a (" + short_line(str(problem), 160) + ")")

        lines.append("These numbers are for orientation in Grafana, not a verdict: a series that is missing while a pod is away is shown as n/a.")

    observability_blocks.append("\n".join(lines) + "\n")


# ---------------------------------------------------------------- before the first tier

def preflight():
    global host, port, topic, group, watcher, poller
    say("[0] Checks before anything is deleted")
    problems = lib.stack_problems()

    if problems:
        raise lib.Fail("the stack is not ready: " + ", ".join(problems) + ". Run:  make status   (and  make diagnose  if a pod is stuck)")

    topic, group = lib.config_value("pipeline-config", "KAFKA_TOPIC"), lib.config_value("processor-config", "CONSUMER_GROUP")
    host, port = lib.rest_target(args.host, args.port)
    status, body = lib.rest_get(host, port, "/stats")

    if status != 200:
        raise lib.Fail("the REST API does not answer from this computer (" + str(status) + " " + body.strip()[:100] + "). If your internet address changed (new Wi-Fi, a hotspot, a VPN), run  make cluster-up  (it updates the firewall)")

    state = load_state()

    if state is None:
        raise lib.Fail("could not read the load generator from the cluster. Run:  make status")

    if not state[0] or state[1] != 5:
        if args.dry_run:
            say("    the load was left on (or the autoscaler left at " + str(state[1]) + ") by an earlier run; a real drill would switch it off first  (make scale-down)")
        else:
            say("    the load was left on (or the autoscaler left at " + str(state[1]) + ") by an earlier run: switching it off first")
            switch("off")

    watcher = PodWatcher()
    poller = RestPoller()
    watcher.start()
    poller.start()
    end = time.monotonic() + 30

    while watcher.latest_at is None and time.monotonic() < end:
        time.sleep(0.2)

    if watcher.latest_at is None:
        raise lib.Fail("could not look at the pods")

    for name, ask in (("prometheus", lambda: lib.prom_number("count(up)")), ("loki", lambda: lib.loki_lines('{namespace="' + lib.NS + '"}', minutes=5, limit=1))):
        try:
            ask()
            monitoring[name] = True
        except lib.Fail:
            monitoring[name] = False

    if args.no_evidence:
        monitoring["prometheus"] = monitoring["loki"] = False

    with lock:
        snapshot = dict(watcher.latest)

    counts = ready_counts(snapshot)
    NEED.update({name: wanted for name, wanted in needs_of(snapshot).items() if counts.get(name, 0) >= wanted})
    short = [name for name in CORE if name not in NEED]

    if short:
        raise lib.Fail("the stack is not whole: " + ", ".join(name + " has " + str(counts.get(name, 0)) + " ready pods, the stack needs " + str(MINIMUM[name]) for name in short) + ". Run:  make status")

    left_out = [name for name in list(MINIMUM) + list(PER_NODE) if name not in NEED]

    if left_out:
        say("    NOT whole at the start, so not counted as part of 'whole' in this drill: " + ", ".join(left_out) + ".  (Run  make status  to see why.)")

    say("    the stack is whole; " + str(len([i for i in snapshot.values() if not i["job"]])) + " pods; the REST API answers; Prometheus " + ("answers" if monitoring["prometheus"] else "does NOT answer (or --no-evidence)")
        + "; Loki " + ("answers" if monitoring["loki"] else "does NOT answer (or --no-evidence)"))

    if not (monitoring["prometheus"] and monitoring["loki"]) and not args.no_evidence:
        say("    (the drill still works; only the Loki and Prometheus part of the evidence is left out. Check:  make metrics-check)")


def dry_run():
    say("\nDry run: nothing is deleted. These pods would be deleted, in this order:\n")

    for tier in chosen:
        spec = SPECS[tier]

        try:
            victim = pick_victim(spec)
            say("  " + tier.ljust(10) + column(victim["name"], 37) + ("(" + ("StatefulSet" if spec["stateful"] else "Deployment") + "; "
                + ("the disk " + spec["disk"] + " stays" if spec.get("disk") else "the Deployment starts a new pod") + ")"))
        except lib.Fail as problem:
            say("  " + tier.ljust(10) + "cannot be drilled now: " + str(problem))

    if "producer" in chosen:
        say("\n  For the producer tier the drill first switches the load generator on with ONE producer pod, and switches it off at the end."
            "\n  The producer pod shown above is the one that exists now (idle); the real run picks the pod that is sending, once the load is on.")


# ---------------------------------------------------------------- the files

def write_files(summary):
    csv_path = out_dir / (prefix + "-rest-calls.csv")

    with open(csv_path, "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["tier", "seconds_after_delete", "clock_utc", "health_status", "health_ms", "stats_status", "stats_ms", "documents"])

        if poller is not None:
            with lock:
                samples = list(poller.samples)

            for r in results:
                if "_t0" not in r:
                    continue

                for s in samples:
                    if r["_t0"] - 2.0 <= s["t"] <= r["_t_end"]:
                        writer.writerow([r["tier"], format(s["t"] - r["_t0"], ".1f"), clock(s["t"]), s["health"] if s["health"] is not None else "no answer", "" if s["health_ms"] is None else format(s["health_ms"], ".0f"),
                                         s["stats"] if s["stats"] is not None else "no answer", "" if s["stats_ms"] is None else format(s["stats_ms"], ".0f"), "" if s["docs"] is None else s["docs"]])

    lines = ["What every pod of the stack did, tier by tier. '+N s' counts from the moment of the delete (kubectl delete pod). Times are UTC.",
             "The pods were looked at every 1.5 seconds, so a time is exact to about 2 seconds. A pod 'gone' is one that was not in the list any more.", ""]

    if watcher is not None:
        with lock:
            changes = list(watcher.changes)

        for r in results:
            if "_t0" not in r:
                continue

            lines.append("=== " + report.name_of(r["tier"]) + ": " + str(r.get("victim")) + " deleted at " + clock(r["_t0"]) + " UTC")

            for moment, name, text in changes:
                if r["_t0"] - 1.0 <= moment <= r["_t_end"]:
                    lines.append(offset(moment - r["_t0"]).rjust(8) + "  " + clock(moment) + "  " + column(name, 41) + text)

            lines.append("")

    (out_dir / (prefix + "-timeline.txt")).write_text("\n".join(lines) + "\n")
    (out_dir / (prefix + "-events.txt")).write_text("Events that Kubernetes wrote during each tier (kubectl get events). '+N s' counts from the delete. Times are UTC.\n\n" + "\n".join(events_blocks))
    (out_dir / (prefix + "-observability.txt")).write_text("Evidence from Loki (logs) and Prometheus (numbers) for each tier.\n\n" + "\n".join(observability_blocks))
    (out_dir / (prefix + "-summary.json")).write_text(json.dumps(summary, indent=2) + "\n")


problem_text = None
finished = False
load_left = False        # True when the load may still be on the cluster after the clean-up

try:
    out_dir.mkdir(parents=True, exist_ok=True)
    sys.stdout = Tee(sys.stdout, Path(os.devnull) if args.dry_run else out_dir / (prefix + "-run.txt"))

    for signal_name in ("SIGINT", "SIGHUP", "SIGTERM"):
        signal.signal(getattr(signal, signal_name), stop_on_signal)

    started_text = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print("Resilience drill: " + ("a pod is deleted in each of " + str(len(chosen)) + " tiers (" + ", ".join(chosen) + ")") + " (namespace " + lib.NS + ")")
    print("Only  kubectl delete pod  is used to break things" + (", and the load generator is switched on and off for the producer tier" if "producer" in chosen else "") + ". Ctrl-C stops the drill and cleans up.\n")
    preflight()

    if args.dry_run:
        dry_run()
        finished = True
    else:
        for number_, tier in enumerate(chosen, 1):
            if time.monotonic() - began > args.max_minutes * 60:
                problem_text = "the limit of --max-minutes " + str(args.max_minutes) + " was reached before the tier " + tier
                break

            r = drill_tier(tier, number_, len(chosen))

            if "_t0" in r and not args.no_evidence:
                pause_for(args.settle)

                try:
                    collect_evidence(r, r["_t0"], r["_t_end"])
                except lib.Fail as problem:
                    say("      (the evidence could not be collected: " + " ".join(str(problem).split())[:150] + ")")
                except Exception:
                    traceback.print_exc()

            if "_t0" in r:
                windows.append({"tier": tier, "from_local": local_clock(r["_t0"] - 180), "to_local": local_clock(time.monotonic() + args.pause + 120)})

            if tier == "producer" and load_switched_on:
                say("      switching the load off again (kubectl apply -k producer/load) ...")
                switch_off()

            if number_ < len(chosen):
                say("      resting " + str(args.pause) + " seconds before the next tier, so the graphs show the recovery ...")
                pause_for(args.pause)

        finished = problem_text is None
except KeyboardInterrupt:
    ignore_signals()
    problem_text = "stopped by the user (Ctrl-C)" if stopped_by[:1] in ([], ["SIGINT"]) else "stopped by a signal (" + stopped_by[0] + ": the window was closed, or the drill was killed)"
    print("\n  Stopped. Cleaning up. This takes a few seconds: pressing Ctrl-C again does nothing now.")
except lib.Fail as problem:
    problem_text = " ".join(str(problem).split())
except Exception:
    traceback.print_exc()
    problem_text = "the drill itself broke (see the error above). Paste it to your assistant."
finally:
    ignore_signals()

    try:
        if load_switched_on:
            switch_off()

        if "producer" in chosen and results and any(x["tier"] == "producer" for x in results) or load_switched_on:
            load_left = check_load_off() is not True
    except Exception as error:
        load_left = True
        print("  Could not switch the load off: " + str(error) + "\n  Run:  make scale-down")

    done.set()

# ---------------------------------------------------------------- the files and the table

if not args.dry_run and results:
    for r in results:
        if r.get("passed") is False and r.get("deleted") and not r.get("problem"):
            r["problem"] = "not every check passed (see above)"

    summary = {"started": started_text, "args": vars(args), "planned": list(chosen), "aborted": problem_text, "load_left": load_left, "monitoring": monitoring, "windows": windows,
               "tiers": [{k: v for k, v in r.items() if not k.startswith("_")} for r in results]}

    try:
        write_files(summary)
        print("\n" + report.render(str(out_dir), prefix))
        print("Files in " + args.out + "/:  " + ", ".join(prefix + s for s in ("-run.txt", "-summary.json", "-timeline.txt", "-events.txt", "-rest-calls.csv", "-observability.txt", "-results.md", "-goals.json", "-chart.svg") if (out_dir / (prefix + s)).exists()))
        tally = report.goal_tally(str(out_dir), prefix)

        if tally:
            print(tally)
    except Exception:
        traceback.print_exc()
        print("The files could not be written completely (see the error above).")

failed = [r for r in results if not r["passed"]]

if args.dry_run and finished:
    print("\nRESULT: DRY RUN - nothing was deleted")
elif finished and results and not failed and not problem_text:
    # The platform did its job. A goal of slo.json can still be missed (a number that is too high): that is a finding, so the line says it.
    try:
        missed = [v["id"] for v in report.judge(summary) if v["verdict"] == "NOT met"]
    except Exception:
        missed = []

    print("\nRESULT: PASS - a pod was deleted in " + str(len(results)) + " of " + str(len(results)) + " tiers; each one was replaced, the stack was whole again and no data was lost"
          + ("" if not missed else ". But " + ("a goal" if len(missed) == 1 else str(len(missed)) + " goals") + " of slo.json was NOT met: " + ", ".join(missed) + " (see the goals table above)"))
else:
    why = problem_text or ("; ".join(r["tier"] + ": " + " ".join(str(r["problem"] or "a check failed").split())[:120] for r in failed) if failed else "the drill did not finish")
    deleted_count = len([r for r in results if r.get("deleted")])

    if problem_text and not [r for r in results if r.get("deleted") and not r["passed"]]:
        # Nothing that was drilled failed: the drill was stopped (or could not start) before the end.
        print("\nRESULT: STOPPED EARLY - " + problem_text + ". "
              + ("No pod had been deleted yet." if not deleted_count else str(deleted_count) + " of " + str(len(chosen)) + " tiers had been drilled, and every one of them passed."))
    else:
        print("\nRESULT: " + ("FAIL" if results else "STOPPED EARLY") + " - " + str(len(failed)) + " of " + str(len(results)) + " tiers did not pass. " + why)

    if load_left:
        print("WARNING: the load may still be running on the cluster. Run:  make scale-down")

    sys.exit(1)

if load_left:
    print("WARNING: the load may still be running on the cluster. Run:  make scale-down")
    sys.exit(1)
