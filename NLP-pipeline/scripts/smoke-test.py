# End-to-end smoke test: sends ONE log line with a unique trace id through the whole pipeline and finds it again at every stage.
#
#   make smoke
#   python3 scripts/smoke-test.py --save evidence/smoke-response.json     (also saves the complete REST answer)
#
#   1. A Job (producer/smoke/job-smoke.yaml) starts a producer pod. It sends one message: the first line of the replay file that carries
#      an alert tag, with the trace id. Kafka answers with the partition and the offset where it stored the message.
#   2. The test reads that exact message back from the Kafka topic (kafka-console-consumer inside the Kafka pod, on its administration listener).
#   3. It looks for the processor's log line with the same trace id, partition and offset.
#   4. It asks MongoDB for the stored line (mongosh inside the MongoDB pod, over TLS, as the administrator).
#   5. It calls the REST API from this computer, over HTTPS with the login, through the node port of the server: GET /events/<trace id>.
# The line is classified like every other line (Drain template, keyword rules, saved LLM decision), so a pass also proves that the models
# inside the processor image load and work. The line is NOT put into a window and does not belong to any replay (its run is "smoke").
# The producer and the processor reach Kafka over TLS with their client certificates, and the processor reaches MongoDB over TLS with its own
# account, so a pass also proves that all of that works. The only change to the stack is one more line in MongoDB. The trace id is new on every run.

import argparse
import json
import sys
import time
import traceback

import nlplib as lib


def say(text):
    print("      " + text)


def ready_count(component):
    return len([p for p in lib.pipeline_pods(component) if lib.pod_is_ready(p)])


# The consumer group's position in every partition, once it has caught up with the end of the log (or None if it has not).
def caught_up(group):
    rows = lib.kafka_group_lag(group)
    return rows if rows and all(r[3] == 0 for r in rows) else None


# What the detectors said about the line, in words, from a stored document or from the processor's log record.
def verdicts(item):
    llm = item.get("llm")
    return ("template " + str(item.get("template_id")) + ", keyword rules " + str(item.get("keyword")) + ", saved LLM decision "
            + ("none for this template" if llm is None else str(llm)))


parser = argparse.ArgumentParser(description="Send one tagged log line through the pipeline and find it at every stage.")
parser.add_argument("--host", help="address of the node to call for the REST step (default: the server, from Terraform)")
parser.add_argument("--port", type=int, help="node port (default: rest_node_port in infra/config.yml)")
parser.add_argument("--save", metavar="FILE", help="write the complete REST answer to this file")
parser.add_argument("--wait", type=int, default=60, help="seconds to wait for the processor to store the line (default 60)")
args = parser.parse_args()

started = time.monotonic()
print("Smoke test: one tagged log line through the whole pipeline (namespace " + lib.NS + ")")

try:
    # ------------------------------------------------------------ 1. is the stack running?
    print("\n[1/6] The stack is running")
    problems = lib.stack_problems()

    if problems:
        raise lib.Fail("the stack is not ready: " + ", ".join(problems) + ". Run:  make status   (and  make diagnose  if a pod is stuck)")

    topic = lib.config_value("pipeline-config", "KAFKA_TOPIC")
    group = lib.config_value("processor-config", "CONSUMER_GROUP")
    say("kafka-0 and mongo-0 are ready; " + str(ready_count("processor")) + " processor pod and " + str(ready_count("restapi")) + " REST API pods are ready")

    # ------------------------------------------------------------ 2. send the line
    print("\n[2/6] Send one log line with a unique trace id")
    trace = lib.new_trace_id()
    say("trace id: " + trace)
    sent = lib.send_event(trace)
    say("the producer pod finished after " + str(round(sent["seconds"])) + " s and printed:")
    say("  " + sent["log"])
    say("Kafka stored the message in partition " + str(sent["partition"]) + " at offset " + str(sent["offset"]) + " of topic " + sent["topic"])

    if sent["topic"] != topic:
        raise lib.Fail("the producer wrote to topic " + sent["topic"] + ", but KAFKA_TOPIC in the ConfigMap is " + topic)

    # ------------------------------------------------------------ 3. is it in Kafka?
    print("\n[3/6] Kafka holds it")
    message = lib.kafka_read(topic, sent["partition"], sent["offset"])
    say("read back from partition " + str(sent["partition"]) + ", offset " + str(sent["offset"]) + " with kafka-console-consumer:")
    say("  " + json.dumps(lib.trimmed(message)))

    if message.get("trace_id") != trace or message.get("kind") != "smoke":
        raise lib.Fail("the message at that place is not ours: trace_id=" + str(message.get("trace_id")) + ", kind=" + str(message.get("kind")))

    shape, ends = lib.kafka_topic_shape(topic), lib.kafka_end_offsets(topic)

    if shape and ends:
        say("topic " + topic + ": " + str(shape[0]) + " partition(s), replication factor " + str(shape[1]) + ", " + str(sum(ends.values())) + " messages in total (" + " + ".join(str(ends[p]) for p in sorted(ends)) + ")")
    else:
        say("(the topic summary is not available: the Kafka tools did not answer)")

    # ------------------------------------------------------------ 4. did the processor read it?
    print("\n[4/6] The processor read it, ran the detectors and stored it")
    line, _ = lib.wait_for(lambda: lib.processor_log_line(trace), args.wait, interval=3)

    if not line:
        raise lib.Fail("no processor log line with trace_id=" + trace + " after " + str(args.wait) + " s. Run:  make logs   and look for errors")

    say(line)
    logged = lib.find_json_line(line, "stored", trace)[1]

    if logged is None or (logged.get("partition"), logged.get("offset")) != (sent["partition"], sent["offset"]):
        raise lib.Fail("the processor stored the line from another place in Kafka than the one the producer reported")

    say("it is the same partition and offset that the producer was told")
    say("the detectors said: " + verdicts(logged))
    rows = lib.wait_for(lambda: caught_up(group), 30, interval=3)[0] or lib.kafka_group_lag(group)

    if rows:
        say("consumer group " + group + ": " + ", ".join("partition " + str(r[0]) + " at " + str(r[1]) + " of " + str(r[2]) + " (behind: " + str(r[3]) + ")" for r in rows))
    else:
        say("(the consumer group summary is not available)")

    # ------------------------------------------------------------ 5. is it in MongoDB?
    print("\n[5/6] MongoDB holds the line")
    record = lib.mongo_lookup(trace)

    if record.get("found") != 1 or not record.get("doc"):
        raise lib.Fail("MongoDB has " + str(record.get("found")) + " lines with this trace id, expected exactly 1")

    stored = record["doc"]
    say("1 line with this trace id: " + json.dumps(lib.trimmed(stored)))
    say("the collection holds " + str(record["total"]) + " lines in total")

    if not stored.get("smoke") or "template_id" not in stored or "keyword" not in stored or "llm" not in stored:
        raise lib.Fail("the stored line has no detector verdicts (template_id, keyword, llm): " + json.dumps(lib.trimmed(stored)))

    if (stored["template_id"], stored["keyword"], stored["llm"]) != (logged.get("template_id"), logged.get("keyword"), logged.get("llm")):
        raise lib.Fail("MongoDB holds other verdicts than the processor logged: " + verdicts(stored) + "  /  " + verdicts(logged))

    # ------------------------------------------------------------ 6. can the REST API return it?
    print("\n[6/6] The REST API returns it to this computer")
    host, port = lib.rest_target(args.host, args.port)
    status, body = lib.rest_get(host, port, "/events/" + trace)
    say("GET /events/" + trace + "    (HTTPS, node port " + str(port) + " of the server; the login is read from the Secret restapi-login, the password is not printed)")
    say("the same request with curl:  curl -s --cacert ca.crt --resolve " + lib.REST_NAME + ":" + str(port) + ":<server-public-ip> -u USER:PASSWORD https://" + lib.REST_NAME + ":" + str(port) + "/events/" + trace + "   (README: Calling the API)")

    if status is None:
        raise lib.Fail("the REST API did not answer: " + body + ". " + lib.ADDRESS_HINT)

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
        raise lib.Fail("the REST API did not return our line (HTTP " + str(status) + "), although MongoDB holds it (stage 5 above). Run:  make diagnose   and look at the restapi pods")

    if answer["event"].get("template_id") != stored["template_id"] or answer["event"].get("keyword") != stored["keyword"]:
        raise lib.Fail("the REST API returned other verdicts than MongoDB holds")

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

print("\nRESULT: PASS - line " + trace + " went producer -> Kafka (partition " + str(sent["partition"]) + ", offset " + str(sent["offset"]) + ") -> processor (detectors) -> MongoDB -> REST API in " + str(round(time.monotonic() - started)) + " s")
print("        To follow the same line through the logs of every program (needs the monitoring):  make logs-trace TRACE=" + trace)
