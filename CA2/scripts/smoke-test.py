# End-to-end smoke test: sends ONE event with a unique trace id through the whole pipeline and finds it again at every stage.
#
#   make smoke
#   python3 scripts/smoke-test.py --save evidence/step6-smoke-response.json     (also saves the complete REST answer)
#
#   1. A Job (producer/smoke/job-smoke.yaml) starts a producer pod. It sends one event: a copy of the first dataset row with
#      Label SMOKE-TEST and the trace id. Kafka answers with the partition and the offset where it stored the event.
#   2. The test reads that exact message back from the Kafka topic (kafka-console-consumer inside the Kafka pod).
#   3. It looks for the processor's log line with the same trace id, partition and offset.
#   4. It asks MongoDB for the record (mongosh inside the MongoDB pod).
#   5. It calls the REST API from this computer, through the node port of the server: GET /events/<trace id>.
# The only change to the stack is one more record in MongoDB. The trace id is new on every run.

import argparse
import json
import re
import sys
import time
import traceback

import ca2lib as lib


def say(text):
    print("      " + text)


def ready_count(component):
    return len([p for p in lib.pipeline_pods(component) if lib.pod_is_ready(p)])


# The consumer group's position in every partition, once it has caught up with the end of the log (or None if it has not).
def caught_up(group):
    rows = lib.kafka_group_lag(group)
    return rows if rows and all(r[3] == 0 for r in rows) else None


parser = argparse.ArgumentParser(description="Send one tagged event through the pipeline and find it at every stage.")
parser.add_argument("--host", help="address of the node to call for the REST step (default: the server, from Terraform)")
parser.add_argument("--port", type=int, help="node port (default: rest_node_port in infra/config.yml)")
parser.add_argument("--save", metavar="FILE", help="write the complete REST answer to this file")
parser.add_argument("--wait", type=int, default=60, help="seconds to wait for the processor to store the event (default 60)")
args = parser.parse_args()

started = time.monotonic()
print("Smoke test: one tagged event through the whole pipeline (namespace " + lib.NS + ")")

try:
    # ------------------------------------------------------------ 1. is the stack running?
    print("\n[1/6] The stack is running")
    problems = lib.stack_problems()

    if problems:
        raise lib.Fail("the stack is not ready: " + ", ".join(problems) + ". Run:  make status   (and  make diagnose  if a pod is stuck)")

    topic = lib.config_value("pipeline-config", "KAFKA_TOPIC")
    group = lib.config_value("processor-config", "CONSUMER_GROUP")
    say("kafka-0 and mongo-0 are ready; " + str(ready_count("processor")) + " processor pod and " + str(ready_count("restapi")) + " REST API pods are ready")

    # ------------------------------------------------------------ 2. send the event
    print("\n[2/6] Send one event with a unique trace id")
    trace = lib.new_trace_id()
    say("trace id: " + trace)
    sent = lib.send_event(trace)
    say("the producer pod finished after " + str(round(sent["seconds"])) + " s and printed:")
    say("  " + sent["log"])
    say("Kafka stored the event in partition " + str(sent["partition"]) + " at offset " + str(sent["offset"]) + " of topic " + sent["topic"])

    if sent["topic"] != topic:
        raise lib.Fail("the producer wrote to topic " + sent["topic"] + ", but KAFKA_TOPIC in the ConfigMap is " + topic)

    # ------------------------------------------------------------ 3. is it in Kafka?
    print("\n[3/6] Kafka holds it")
    message = lib.kafka_read(topic, sent["partition"], sent["offset"])
    say("read back from partition " + str(sent["partition"]) + ", offset " + str(sent["offset"]) + " with kafka-console-consumer:")
    say("  " + json.dumps(lib.trimmed(message)))

    if message.get("trace_id") != trace or message.get("Label") != "SMOKE-TEST":
        raise lib.Fail("the message at that place is not ours: trace_id=" + str(message.get("trace_id")) + ", Label=" + str(message.get("Label")))

    shape, ends = lib.kafka_topic_shape(topic), lib.kafka_end_offsets(topic)

    if shape and ends:
        say("topic " + topic + ": " + str(shape[0]) + " partitions, replication factor " + str(shape[1]) + ", " + str(sum(ends.values())) + " messages in total (" + " + ".join(str(ends[p]) for p in sorted(ends)) + ")")
    else:
        say("(the topic summary is not available: the Kafka tools did not answer)")

    # ------------------------------------------------------------ 4. did the processor read it?
    print("\n[4/6] The processor read it and stored it")
    line, _ = lib.wait_for(lambda: lib.processor_log_line(trace), args.wait, interval=3)

    if not line:
        raise lib.Fail("no processor log line with trace_id=" + trace + " after " + str(args.wait) + " s. Run:  make logs   and look for errors")

    say(line)
    place = re.search(r"partition=(\d+) offset=(\d+) stored", line)

    if not place or (int(place.group(1)), int(place.group(2))) != (sent["partition"], sent["offset"]):
        raise lib.Fail("the processor stored the event from another place in Kafka than the one the producer reported")

    say("it is the same partition and offset that the producer was told")
    rows = lib.wait_for(lambda: caught_up(group), 30, interval=3)[0] or lib.kafka_group_lag(group)

    if rows:
        say("consumer group " + group + ": " + ", ".join("partition " + str(r[0]) + " at " + str(r[1]) + " of " + str(r[2]) + " (behind: " + str(r[3]) + ")" for r in rows))
    else:
        say("(the consumer group summary is not available)")

    # ------------------------------------------------------------ 5. is it in MongoDB?
    print("\n[5/6] MongoDB holds the record")
    record = lib.mongo_lookup(trace)

    if record.get("found") != 1 or not record.get("doc"):
        raise lib.Fail("MongoDB has " + str(record.get("found")) + " records with this trace id, expected exactly 1")

    say("1 record with this trace id: " + json.dumps(lib.trimmed(record["doc"])))
    say("the collection holds " + str(record["total"]) + " records in total")

    # ------------------------------------------------------------ 6. can the REST API return it?
    print("\n[6/6] The REST API returns it to this computer")
    host, port = lib.rest_target(args.host, args.port)
    status, body = lib.rest_get(host, port, "/events/" + trace)
    say("GET /events/" + trace + "    (node port " + str(port) + " of the server)")
    say("the same request with curl:  curl -s http://<server-public-ip>:" + str(port) + "/events/" + trace)

    if status is None:
        raise lib.Fail("the REST API did not answer: " + body + ". If your internet address changed (new Wi-Fi, a hotspot, a VPN), run:  make cluster-up")

    try:
        answer = json.loads(body)
    except ValueError:
        raise lib.Fail("the REST API answered HTTP " + str(status) + " with something that is not JSON: " + body.strip()[:200])

    say("answer: HTTP " + str(status))

    if status == 200 and answer.get("event"):
        say("  " + json.dumps({"found": answer.get("found"), "event": lib.trimmed(answer["event"])}))
    else:
        say("  " + body.strip()[:300])

    if status != 200 or not answer.get("found") or answer.get("event", {}).get("trace_id") != trace:
        raise lib.Fail("the REST API did not return our event (HTTP " + str(status) + "), although MongoDB holds it (step 5). Run:  make diagnose   and look at the restapi pods")

    if args.save:
        with open(args.save, "w") as f:
            json.dump(answer, f, indent=2)
            f.write("\n")

        say("the complete answer (" + str(len(answer["event"])) + " fields) is saved in " + args.save)

    status, body = lib.rest_get(host, port, "/stats")
    say("GET /stats  ->  HTTP " + str(status) + " " + body.strip())

except lib.Fail as problem:
    print("\nRESULT: FAIL - " + str(problem))
    sys.exit(1)
except Exception:
    traceback.print_exc()
    print("\nRESULT: FAIL - the test itself broke (see the error above). Paste it to your assistant.")
    sys.exit(1)

print("\nRESULT: PASS - event " + trace + " went producer -> Kafka (partition " + str(sent["partition"]) + ", offset " + str(sent["offset"]) + ") -> processor -> MongoDB -> REST API in " + str(round(time.monotonic() - started)) + " s")
