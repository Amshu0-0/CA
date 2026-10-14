# Helpers shared by the three test scripts: smoke-test.py, netpol-check.py and heal-check.py.
#
# They run kubectl, wait for something to happen, call the REST API from this computer, and send one tagged event
# through the pipeline and find it again at every stage. They need only python3 and kubectl
# (and terraform, to find the public address of the server node).
#
# Settings come from the environment, so the Makefile can pass them on:
#   KUBECTL    the kubectl program (default: kubectl)
#   NS         the namespace of the pipeline (default: ca2)
#   TERRAFORM  the terraform program (default: terraform)

import json
import os
import re
import secrets
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KUBECTL = os.environ.get("KUBECTL", "kubectl")
NS = os.environ.get("NS", "ca2")
JOB = "producer-smoke"
CHAIN = "app.kubernetes.io/part-of=ca2-pipeline"


# A check did not pass. The message says what to look at.
class Fail(Exception):
    pass


# ---------------------------------------------------------------- kubectl

# Run kubectl and return (exit code, standard output, error output). A failing kubectl is not an exception:
# the caller decides what a failure means. A missing or hanging kubectl is.
def kubectl(*args, input=None, timeout=60):
    try:
        done = subprocess.run([KUBECTL, *args], input=input, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise Fail("kubectl was not found. Install it, or set KUBECTL=/path/to/kubectl")
    except subprocess.TimeoutExpired:
        raise Fail("kubectl " + " ".join(args[:6]) + " did not finish within " + str(timeout) + " seconds")

    return done.returncode, done.stdout, done.stderr


# Ask kubectl for objects as JSON. A failure is an exception, with kubectl's own message.
def kube_json(*args, timeout=60):
    code, out, err = kubectl(*args, "-o", "json", timeout=timeout)

    if code != 0:
        raise Fail("kubectl " + " ".join(args) + " failed: " + (err.strip() or out.strip() or "no message"))

    return json.loads(out)


# Call check() every few seconds until it returns something true, or the time is up.
# Returns (what check() returned, seconds waited); the first value is None when the time ran out.
def wait_for(check, timeout, interval=2.0):
    start = time.monotonic()

    while True:
        value = check()

        if value:
            return value, time.monotonic() - start

        if time.monotonic() - start >= timeout:
            return None, time.monotonic() - start

        time.sleep(interval)


def pod_is_ready(pod):
    if pod.get("metadata", {}).get("deletionTimestamp"):
        return False

    return any(c.get("type") == "Ready" and c.get("status") == "True" for c in pod.get("status", {}).get("conditions", []))


# The pods of one pipeline component, found by the label app.kubernetes.io/name. Test pods carry another part-of label,
# so they are never counted as part of the pipeline.
def pipeline_pods(name):
    selector = "app.kubernetes.io/name=" + name + "," + CHAIN
    return kube_json("-n", NS, "get", "pods", "-l", selector)["items"]


def pod_names(items):
    return sorted(p["metadata"]["name"] for p in items)


# Why the stack cannot be tested right now. An empty list means that everything is running.
def stack_problems():
    problems = []
    named = {p["metadata"]["name"]: p for p in kube_json("-n", NS, "get", "pods")["items"]}

    for name in ("kafka-0", "mongo-0"):
        if name not in named or not pod_is_ready(named[name]):
            problems.append(name + " is not ready")

    for name in ("processor", "restapi"):
        if not any(pod_is_ready(p) for p in pipeline_pods(name)):
            problems.append("no " + name + " pod is ready")

    return problems


def config_value(configmap, key):
    code, out, err = kubectl("-n", NS, "get", "configmap", configmap, "-o", "jsonpath={.data." + key + "}")

    if code != 0 or not out.strip():
        raise Fail("could not read " + key + " from the ConfigMap " + configmap + ": " + (err.strip() or "empty"))

    return out.strip()


# ---------------------------------------------------------------- the REST API, called from this computer

# The public addresses of the machines come from Terraform, so no address is ever typed into this project.
def node_addresses():
    terraform = os.environ.get("TERRAFORM", "terraform")
    out = subprocess.run([terraform, "-chdir=" + str(ROOT / "infra" / "terraform"), "output", "-json"], capture_output=True, text=True)

    if out.returncode != 0:
        raise Fail("Could not ask Terraform for the node addresses:\n" + out.stderr.strip())

    values = json.loads(out.stdout or "{}")

    if "server_public_ip" not in values or "agent_public_ips" not in values:
        raise Fail("Terraform knows no machines, so there is no cluster to call. Build one with:  make cluster-up")

    return [values["server_public_ip"]["value"]] + list(values["agent_public_ips"]["value"])


# The node port is written once, in infra/config.yml (make verify makes sure it equals the Service).
def node_port():
    found = re.search(r"^rest_node_port:\s*(\d+)", (ROOT / "infra" / "config.yml").read_text(), flags=re.M)
    return int(found.group(1)) if found else 30080


# One GET request. Returns (status code, body text), or (None, reason) when nobody answered.
def rest_get(host, port, path, timeout=5):
    try:
        with urllib.request.urlopen("http://" + host + ":" + str(port) + path, timeout=timeout) as answer:
            return answer.status, answer.read().decode()
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode()
    except Exception as error:
        return None, error.__class__.__name__ + ": " + str(error)


# Which address and port to call: the ones given, or the server node's address from Terraform.
def rest_target(host=None, port=None):
    return host or node_addresses()[0], port or node_port()


# ---------------------------------------------------------------- one tagged event through the pipeline

def new_trace_id():
    return "smoke-" + datetime.now(timezone.utc).strftime("%m%d-%H%M%S") + "-" + secrets.token_hex(2)


def job_log():
    return kubectl("-n", NS, "logs", "job/" + JOB, "-c", "producer")[1]


# Why the Job does not finish, in a few lines: what each of its containers is doing or waiting for.
def job_trouble():
    code, out, err = kubectl("-n", NS, "get", "pods", "-l", "job-name=" + JOB, "-o", "json")

    try:
        pods = json.loads(out)["items"]
    except (ValueError, KeyError):
        return "(could not list the pods of the Job: " + (err.strip() or "no answer") + ")"

    if not pods:
        return "The Job has no pod. Kubernetes has not started one."

    lines = []

    for p in pods:
        status = p.get("status", {})
        lines.append("pod " + p["metadata"]["name"] + ": " + status.get("phase", "?"))

        for c in status.get("initContainerStatuses", []) + status.get("containerStatuses", []):
            state = c.get("state", {})

            if "waiting" in state:
                lines.append("  " + c["name"] + " is waiting: " + state["waiting"].get("reason", "?") + (" - " + state["waiting"].get("message", "")[:160] if state["waiting"].get("message") else ""))
            elif "terminated" in state:
                lines.append("  " + c["name"] + " ended: " + state["terminated"].get("reason", "?") + ", exit code " + str(state["terminated"].get("exitCode")))
            elif "running" in state:
                lines.append("  " + c["name"] + " is running")

    wait_log = kubectl("-n", NS, "logs", "job/" + JOB, "-c", "wait-for-kafka", "--tail=2")[1].strip()

    if wait_log:
        lines.append("  the init container wait-for-kafka says: " + " / ".join(wait_log.splitlines()))
        lines.append("  If it keeps saying 'waiting for kafka', the pod cannot reach Kafka: run  make netpol-check  and  make status")

    return "\n".join(lines)


# Create the Job that sends one event carrying this trace id, wait until it has finished, and return where Kafka stored
# the event: {"topic", "partition", "offset", "log", "seconds"}.
def send_event(trace, timeout=240):
    manifest = (ROOT / "producer" / "smoke" / "job-smoke.yaml").read_text()

    if "__TRACE_ID__" not in manifest:
        raise Fail("producer/smoke/job-smoke.yaml has no __TRACE_ID__ to replace")

    kubectl("-n", NS, "delete", "job", JOB, "--ignore-not-found", "--wait=true", timeout=120)
    code, out, err = kubectl("create", "-f", "-", input=manifest.replace("__TRACE_ID__", trace))

    if code != 0:
        raise Fail("could not create the producer Job:\n" + err.strip())

    start = time.monotonic()

    while True:
        status = kube_json("-n", NS, "get", "job", JOB).get("status", {})
        failed = [c for c in status.get("conditions", []) if c.get("type") == "Failed" and c.get("status") == "True"]

        if status.get("succeeded", 0) >= 1:
            break

        if failed:
            raise Fail("the producer Job failed (" + failed[0].get("reason", "?") + "). Its log:\n" + (job_log().strip() or "(empty)") + "\n" + job_trouble() + "\nRun:  make diagnose")

        if time.monotonic() - start > timeout:
            raise Fail("the producer Job did not finish within " + str(timeout) + " seconds:\n" + job_trouble() + "\nRun:  make diagnose")

        time.sleep(2)

    log = job_log()
    found = re.search(r"sent trace_id=(\S+) topic=(\S+) partition=(\d+) offset=(\d+)", log)

    if not found or found.group(1) != trace:
        raise Fail("the producer finished, but its log has no 'sent trace_id=" + trace + " ...' line:\n" + log.strip())

    return {"topic": found.group(2), "partition": int(found.group(3)), "offset": int(found.group(4)), "log": found.group(0), "seconds": time.monotonic() - start}


# Run a command inside the Kafka pod. Its command line tools are in the image. A small memory limit keeps them from
# competing with the broker, which was started with its own, larger heap setting.
def kafka_exec(command, timeout=90):
    return kubectl("-n", NS, "exec", "kafka-0", "-c", "kafka", "--", "sh", "-c", 'export KAFKA_HEAP_OPTS="-Xms64m -Xmx128m"; ' + command, timeout=timeout)


# Read exactly one message back from the topic: the one at this partition and offset.
def kafka_read(topic, partition, offset):
    code, out, err = kafka_exec("kafka-console-consumer --bootstrap-server localhost:9092 --topic " + topic + " --partition " + str(partition) + " --offset " + str(offset) + " --max-messages 1 --timeout-ms 20000")

    for line in out.splitlines():
        if line.startswith("{"):
            try:
                return json.loads(line)
            except ValueError:
                break

    raise Fail("Kafka returned no message at partition " + str(partition) + ", offset " + str(offset) + " of topic " + topic + ".\nexit code " + str(code) + "\n" + (err.strip() or out.strip() or "no output")[-600:])


# {partition: number of the next offset}; the sum is the number of messages in the topic. None when the tool is not in the image.
def kafka_end_offsets(topic):
    for command in ("kafka-get-offsets --bootstrap-server localhost:9092 --topic " + topic,
                    "kafka-run-class kafka.tools.GetOffsetShell --bootstrap-server localhost:9092 --topic " + topic + " --time -1"):
        code, out, err = kafka_exec(command)
        found = re.findall(r"^" + re.escape(topic) + r":(\d+):(\d+)\s*$", out, flags=re.M)

        if code == 0 and found:
            return {int(p): int(o) for p, o in found}

    return None


# (number of partitions, replication factor) of the topic, or None.
def kafka_topic_shape(topic):
    code, out, err = kafka_exec("kafka-topics --bootstrap-server localhost:9092 --describe --topic " + topic)
    partitions, replicas = re.search(r"PartitionCount:\s*(\d+)", out), re.search(r"ReplicationFactor:\s*(\d+)", out)

    return (int(partitions.group(1)), int(replicas.group(1))) if partitions and replicas else None


# [(partition, current offset, end offset, lag)] of the consumer group, or None. A number is None where Kafka printed "-".
def kafka_group_lag(group):
    code, out, err = kafka_exec("kafka-consumer-groups --bootstrap-server localhost:9092 --describe --group " + group)
    rows = []

    for line in out.splitlines():
        t = line.split()

        if len(t) >= 6 and t[0] == group and t[2].isdigit():
            rows.append((int(t[2]),) + tuple(int(x) if x.isdigit() else None for x in t[3:6]))

    return rows or None


# The processor's log line for this trace id: [TRACE] trace_id=... partition=... offset=... stored (or None).
def processor_log_line(trace, since="15m"):
    selector = "app.kubernetes.io/name=processor," + CHAIN
    code, out, err = kubectl("-n", NS, "logs", "-l", selector, "-c", "processor", "--since=" + since, "--tail=-1", "--prefix", timeout=60)

    for line in out.splitlines():
        if "trace_id=" + trace + " " in line:
            return line.strip()

    return None


# Ask MongoDB, from inside its own pod, for the record with this trace id: {"total", "found", "doc"}.
# The user name and password are read from the mounted Secret by the shell inside the pod. They never reach this computer.
def mongo_lookup(trace):
    database, collection = config_value("pipeline-config", "MONGO_DB"), config_value("pipeline-config", "MONGO_COLLECTION")
    script = ('const c = db.getSiblingDB("' + database + '").getCollection("' + collection + '"); '
              'print(JSON.stringify({total: Number(c.estimatedDocumentCount()), found: Number(c.countDocuments({trace_id: "' + trace + '"})), '
              'doc: c.findOne({trace_id: "' + trace + '"}, {_id: 0})}))')
    shell = 'mongosh --quiet -u "$(cat /etc/secrets/mongo/username)" -p "$(cat /etc/secrets/mongo/password)" --authenticationDatabase admin --eval "$1"'
    code, out, err = kubectl("-n", NS, "exec", "mongo-0", "-c", "mongo", "--", "sh", "-c", shell, "sh", script, timeout=90)

    for line in reversed(out.splitlines()):
        if line.startswith("{"):
            return json.loads(line)

    raise Fail("mongosh in mongo-0 gave no answer.\nexit code " + str(code) + "\n" + (err.strip() or out.strip() or "no output")[-600:])


# A short view of a record: the fields that tell a reader what it is, then a count of the others.
def trimmed(event, keep=("trace_id", "Label", "Source IP", "Destination IP", "Destination Port", "Protocol", "Timestamp")):
    short = {k: event[k] for k in keep if k in event}
    short["..."] = str(len(event) - len(short)) + " more fields"

    return short
