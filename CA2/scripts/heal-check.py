# Kills parts of the running stack on purpose and checks that Kubernetes brings them back and that no data is lost.
#
#   make heal-check                              all four tests, about 5 minutes
#   make heal-check HEAL_ARGS="--only mongo"     one test (rest, processor, mongo or kafka)
#
#   rest       delete one of the two REST API pods while this computer keeps calling the API
#   processor  stop the processor, send an event (it waits in Kafka), start the processor: the event arrives
#   mongo      delete the MongoDB pod: the new pod gets the same disk, and every record is still there
#   kafka      delete the Kafka pod: the log is still on its disk, and a new event still travels through the whole pipeline
#
# The tests use the same tagged event as the smoke test (scripts/smoke-test.py). Whatever happens, the processor is switched
# back on at the end.

import argparse
import json
import sys
import threading
import time
import traceback

import ca2lib as lib

ALL = ("rest", "processor", "mongo", "kafka")

parser = argparse.ArgumentParser(description="Kill pods on purpose and check that Kubernetes recovers.")
parser.add_argument("--only", help="comma-separated tests to run: rest, processor, mongo, kafka (default: all four)")
parser.add_argument("--host", help="address of the node to call for the REST API (default: the server, from Terraform)")
parser.add_argument("--port", type=int, help="node port (default: rest_node_port in infra/config.yml)")
args = parser.parse_args()

if args.only and not set(args.only.split(",")) <= set(ALL):
    sys.exit("--only takes a comma-separated list of: " + ", ".join(ALL))

chosen = [t for t in ALL if not args.only or t in args.only.split(",")]
host = port = None
known = {}      # what a test leaves for the next one: the trace id of an event that reached MongoDB
results = []    # (test, passed, one line)


def say(text):
    print("      " + text)


def pod(name):
    found = [p for p in lib.kube_json("-n", lib.NS, "get", "pods")["items"] if p["metadata"]["name"] == name]
    return found[0] if found else None


def ready_pods(component):
    return [p for p in lib.pipeline_pods(component) if lib.pod_is_ready(p)]


# Does the REST API answer on /health? A call that takes longer than 2 seconds counts as no.
def api_up():
    return lib.rest_get(host, port, "/health", timeout=2)[0] == 200


# The number of records in MongoDB, as the REST API reports it (None while the API cannot answer).
def stats():
    status, body = lib.rest_get(host, port, "/stats")
    return int(json.loads(body)["documents"]) if status == 200 else None


def event_status(trace):
    return lib.rest_get(host, port, "/events/" + trace)[0]


# Send a new tagged event. Returns its trace id and where Kafka stored it.
def new_event():
    trace = lib.new_trace_id()
    return trace, lib.send_event(trace)


# Wait until the REST API returns the event. Returns the seconds it took, or None.
def wait_in_rest(trace, timeout=120):
    found, seconds = lib.wait_for(lambda: event_status(trace) == 200, timeout, interval=2)
    return seconds if found else None


# Wait until the named pod is a new one (another uid than before) and ready. Returns the seconds it took, or None.
def wait_replaced(name, old_uid, timeout):
    def replaced():
        p = pod(name)
        return p is not None and p["metadata"]["uid"] != old_uid and lib.pod_is_ready(p)

    found, seconds = lib.wait_for(replaced, timeout, interval=2)
    return seconds if found else None


def disk(claim):
    found = lib.kube_json("-n", lib.NS, "get", "pvc", claim)
    return found["spec"]["volumeName"], found["status"]["phase"]


# ---------------------------------------------------------------- test 1: a REST API pod dies

def test_rest():
    pods = ready_pods("restapi")

    if len(pods) < 2:
        raise lib.Fail("this test needs 2 ready REST API pods, found " + str(len(pods)))

    victim, original = pods[0]["metadata"]["name"], set(lib.pod_names(pods))
    samples, polling = [], threading.Event()
    polling.set()

    def poll():
        while polling.is_set():
            samples.append((time.monotonic(), api_up()))
            time.sleep(0.4)

    thread = threading.Thread(target=poll, daemon=True)
    thread.start()
    time.sleep(2)
    say("deleting " + victim + " while this computer calls GET /health every 0.4 seconds...")
    start = time.monotonic()
    lib.kubectl("-n", lib.NS, "delete", "pod", victim, "--wait=false")
    back, seconds = lib.wait_for(lambda: len(ready_pods("restapi")) == 2 and victim not in lib.pod_names(ready_pods("restapi")), 120, interval=2)
    time.sleep(2)
    polling.clear()
    thread.join()
    calls = [s for s in samples if s[0] >= start]
    failed = [s for s in calls if not s[1]]
    longest, since = 0.0, None

    for moment, up in calls:
        since = (moment if since is None else since) if not up else None
        longest = max(longest, (moment - since) if since is not None else 0.0)

    if not back:
        raise lib.Fail("the REST API did not get back to 2 ready pods within 120 s. Run:  make diagnose")

    fresh = [n for n in lib.pod_names(ready_pods("restapi")) if n not in original]
    say("Kubernetes started a new pod (" + ", ".join(fresh) + "); both REST API pods are ready again " + str(round(seconds)) + " s after the delete")
    say("calls made meanwhile: " + str(len(calls)) + ", failed: " + str(len(failed)) + (" (longest gap " + format(longest, ".1f") + " s)" if failed else ""))

    if not api_up():
        raise lib.Fail("the REST API does not answer after the pods came back")

    return "one REST pod deleted, 2 ready again after " + str(round(seconds)) + " s, " + str(len(failed)) + " of " + str(len(calls)) + " calls failed"


# ---------------------------------------------------------------- test 2: the processor is away, Kafka keeps the event

def test_processor():
    try:
        say("scaling the processor down to 0 pods...")
        lib.kubectl("-n", lib.NS, "scale", "deployment/processor", "--replicas=0")

        if not lib.wait_for(lambda: not lib.pipeline_pods("processor"), 90, interval=2)[0]:
            raise lib.Fail("the processor pod did not go away within 90 s")

        trace, sent = new_event()
        say("with no processor running, the producer sent " + trace)
        say("Kafka stored it at partition " + str(sent["partition"]) + ", offset " + str(sent["offset"]))
        lib.kafka_read(lib.config_value("pipeline-config", "KAFKA_TOPIC"), sent["partition"], sent["offset"])
        say("it is in Kafka (read back with kafka-console-consumer)")
        before = event_status(trace)
        say("the REST API says: HTTP " + str(before) + " (the event is not in MongoDB: nobody reads Kafka)")

        if before != 404:
            raise lib.Fail("expected HTTP 404 while the processor is away, got " + str(before))

        say("scaling the processor up to 1 pod...")
        start = time.monotonic()
        lib.kubectl("-n", lib.NS, "scale", "deployment/processor", "--replicas=1")

        if wait_in_rest(trace, 180) is None:
            raise lib.Fail("the event did not reach MongoDB within 180 s after the processor came back. Run:  make logs")

        took = round(time.monotonic() - start)
        say("HTTP 200: the new processor read the waiting event from Kafka and stored it, " + str(took) + " s after it was started")
        known["trace"] = trace

        return "an event sent while the processor was away reached MongoDB " + str(took) + " s after the processor started"
    finally:
        lib.kubectl("-n", lib.NS, "scale", "deployment/processor", "--replicas=1")


# ---------------------------------------------------------------- test 3: MongoDB dies, the disk keeps the data

def test_mongo():
    if "trace" not in known:
        trace, _ = new_event()

        if wait_in_rest(trace) is None:
            raise lib.Fail("could not store a first test event. Run:  make smoke")

        known["trace"] = trace

    trace, before, old, claim = known["trace"], stats(), pod("mongo-0")["metadata"]["uid"], disk("data-mongo-0")
    say("before: " + str(before) + " records in MongoDB, the disk is " + claim[0] + " (" + claim[1] + ")")
    say("deleting mongo-0...")
    lib.kubectl("-n", lib.NS, "delete", "pod", "mongo-0", "--wait=false")
    seconds = wait_replaced("mongo-0", old, 240)

    if seconds is None:
        raise lib.Fail("a new mongo-0 was not ready within 240 s. Run:  make diagnose")

    again = disk("data-mongo-0")
    say("a new mongo-0 is ready after " + str(round(seconds)) + " s, and it uses the disk " + again[0] + " (" + again[1] + ")")

    if again[0] != claim[0]:
        raise lib.Fail("the new pod got another disk")

    if not lib.wait_for(lambda: stats() is not None, 120, interval=3)[0]:
        raise lib.Fail("the REST API did not get back to MongoDB within 120 s. Run:  make diagnose")

    after = stats()
    say("after: " + str(after) + " records; GET /events/" + trace + " -> HTTP " + str(event_status(trace)))

    if after != before or event_status(trace) != 200:
        raise lib.Fail("data is missing: " + str(before) + " records before, " + str(after) + " after")

    return "mongo-0 deleted, a new pod was ready after " + str(round(seconds)) + " s with the same disk, all " + str(after) + " records still there"


# ---------------------------------------------------------------- test 4: Kafka dies, the log is on its disk

def test_kafka():
    topic = lib.config_value("pipeline-config", "KAFKA_TOPIC")
    before, old, claim = lib.kafka_end_offsets(topic), pod("kafka-0")["metadata"]["uid"], disk("data-kafka-0")
    say("before: " + (str(sum(before.values())) + " messages in topic " + topic if before else "(message count not available)") + ", the disk is " + claim[0] + " (" + claim[1] + ")")
    say("deleting kafka-0...")
    lib.kubectl("-n", lib.NS, "delete", "pod", "kafka-0", "--wait=false")
    seconds = wait_replaced("kafka-0", old, 300)

    if seconds is None:
        raise lib.Fail("a new kafka-0 was not ready within 300 s. Run:  make diagnose")

    again = disk("data-kafka-0")
    after = lib.wait_for(lambda: lib.kafka_end_offsets(topic), 90, interval=3)[0]
    say("a new kafka-0 is ready after " + str(round(seconds)) + " s, and it uses the disk " + again[0] + " (" + again[1] + ")")
    say((str(sum(after.values())) + " messages in the topic") if after else "the topic does not answer")

    if again[0] != claim[0]:
        raise lib.Fail("the new pod got another disk")

    if before and after and sum(after.values()) != sum(before.values()):
        raise lib.Fail("the topic held " + str(sum(before.values())) + " messages before and " + str(sum(after.values())) + " after")

    trace, sent = new_event()
    say("a new event went through the restarted broker: partition " + str(sent["partition"]) + ", offset " + str(sent["offset"]))
    arrived = wait_in_rest(trace, 240)

    if arrived is None:
        raise lib.Fail("the new event did not reach MongoDB within 240 s: the processor did not find the restarted broker. Run:  make logs")

    say("HTTP 200 from the REST API: producer, Kafka, processor, MongoDB and REST API all work again")
    known["trace"] = trace

    return "kafka-0 deleted, a new pod was ready after " + str(round(seconds)) + " s with the same disk and the same messages, and a new event went all the way through"


TESTS = {"rest": ("A REST API pod dies", test_rest), "processor": ("The processor is away, Kafka keeps the event", test_processor),
         "mongo": ("MongoDB dies, the disk keeps the data", test_mongo), "kafka": ("Kafka dies, the log is on its disk", test_kafka)}

print("Self-healing test: " + ", ".join(chosen))

try:
    problems = lib.stack_problems()

    if problems:
        raise lib.Fail("the stack is not ready: " + ", ".join(problems) + ". Run:  make status")

    host, port = lib.rest_target(args.host, args.port)

    if not api_up():
        raise lib.Fail("the REST API does not answer from this computer. If your internet address changed (new Wi-Fi, a hotspot, a VPN), run:  make cluster-up")
except lib.Fail as problem:
    print("\nRESULT: FAIL - " + str(problem))
    sys.exit(1)

for number, key in enumerate(chosen, 1):
    title, run = TESTS[key]
    print("\n[" + str(number) + "/" + str(len(chosen)) + "] " + title)

    try:
        results.append((key, True, run()))
    except lib.Fail as problem:
        say("FAIL: " + str(problem))
        results.append((key, False, str(problem).splitlines()[0]))
    except Exception:
        traceback.print_exc()
        results.append((key, False, "the test itself broke (see the error above)"))

    # The next test starts from a stack that is whole again.
    lib.wait_for(lambda: not lib.stack_problems(), 180, interval=3)

print("\nSummary")

for key, passed, line in results:
    print("  " + ("PASS" if passed else "FAIL") + "  " + key.ljust(11) + line)

if all(passed for _, passed, _ in results):
    print("\nRESULT: PASS - " + str(len(results)) + " of " + str(len(results)) + " recovery tests passed")
else:
    print("\nRESULT: FAIL - " + str(sum(1 for r in results if not r[1])) + " of " + str(len(results)) + " recovery tests failed")
    sys.exit(1)
