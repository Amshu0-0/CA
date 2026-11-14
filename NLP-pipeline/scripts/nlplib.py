# Helpers shared by the test scripts: smoke-test.py, rest-check.py, results.py, netpol-check.py, heal-check.py and metrics-check.py.
#
# They run kubectl, wait for something to happen, call the REST API from this computer, and send one tagged log line
# through the pipeline and find it again at every stage. They need only python3 and kubectl
# (and terraform, to find the public address of the server node).
#
# The stack speaks TLS everywhere, so the helpers do too:
#   - The REST API is called over HTTPS. The CA certificate and the login are read from the Secrets pki-ca and restapi-login with kubectl
#     (the person who runs the tests is the administrator of the cluster). The password is never printed.
#   - The Kafka tools run inside the Kafka pod, on the administration listener 127.0.0.1:9094 (loopback only, no TLS).
#   - mongosh runs inside the MongoDB pod, over TLS, as the administrator.
#
# Settings come from the environment, so the Makefile can pass them on:
#   KUBECTL    the kubectl program (default: kubectl)
#   NS         the namespace of the pipeline (default: nlp)
#   TERRAFORM  the terraform program (default: terraform)

import base64
import http.client
import json
import os
import re
import secrets
import socket
import ssl
import subprocess
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KUBECTL = os.environ.get("KUBECTL", "kubectl")
NS = os.environ.get("NS", "nlp")
JOB = "producer-smoke"
CHAIN = "app.kubernetes.io/part-of=nlp-pipeline"

# Where the Kafka tools in the Kafka pod connect: the administration listener, loopback only (see kafka/kafka.yaml).
KAFKA_ADMIN = "127.0.0.1:9094"

# The name that the REST API's certificate carries for calls from outside the cluster (bootstrap.py). The tests connect to the address of
# a node but check the certificate against this name.
REST_NAME = "restapi.nlp.test"


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

    code, out, err = kubectl("-n", NS, "get", "job", "bootstrap", "-o", "jsonpath={.status.succeeded}")

    if code != 0 or out.strip() != "1":
        problems.append("the Job bootstrap (passwords and certificates) has not finished")

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


# The firewall of the cluster lets in one internet address: the one that was current at the last  make cluster-up . After a new Wi-Fi, a phone hotspot or
# a VPN this computer is outside, and kubectl (like the REST API) just times out. That looks like a stack that is down, but it is not.
ADDRESS_HINT = "If your internet address changed (new Wi-Fi, a phone hotspot, a VPN), run  make cluster-up  to update the firewall, then try again."


# True when the text of a kubectl error says that the cluster could not be reached at all (as opposed to: reached, but it has no such object).
def cannot_connect(err):
    return any(w in err for w in ("i/o timeout", "Unable to connect to the server", "connection refused", "no route to host", "TLS handshake timeout", "context deadline exceeded"))


# One value of a Secret, read with kubectl (the administrator may; nobody else in the cluster can). Returned as bytes.
def secret_value(secret, key):
    code, out, err = kubectl("-n", NS, "get", "secret", secret, "-o", "jsonpath={.data." + key.replace(".", "\\.") + "}")

    if code != 0 or not out.strip():
        hint = (ADDRESS_HINT + " If it did not change, is the cluster running? Run:  make cluster-status") if cannot_connect(err) else "Is the stack up? Run:  make up"
        raise Fail("could not read " + key + " from the Secret " + secret + ": " + (err.strip() or "empty") + ". " + hint)

    return base64.b64decode(out.strip())


_rest_cache = {}


# The CA certificate of the stack (public): what a client needs to check the REST API's certificate.
def rest_ca():
    if "ca" not in _rest_cache:
        _rest_cache["ca"] = secret_value("pki-ca", "ca.crt").decode()

    return _rest_cache["ca"]


# The REST API login: (user name, password). The password stays in memory and is never printed.
def rest_login():
    if "login" not in _rest_cache:
        _rest_cache["login"] = (secret_value("restapi-login", "username").decode(), secret_value("restapi-login", "password").decode())

    return _rest_cache["login"]


# An HTTPS connection to a node's address that checks the certificate against the name REST_NAME instead of the address.
class _Connection(http.client.HTTPSConnection):
    def connect(self):
        sock = socket.create_connection((self.host, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=REST_NAME)


# One GET request over HTTPS. Returns (status code, body text), or (None, reason) when nobody answered.
#   login=False   send no login (the answer must be 401 everywhere except /health and /ready)
#   user, password  give another login than the real one
#   ca=False      do not trust the stack's CA (the handshake must fail: the certificate is not from a public CA)
def rest_get(host, port, path, timeout=5, login=True, user=None, password=None, ca=True):
    try:
        context = ssl.create_default_context(cadata=rest_ca()) if ca else ssl.create_default_context()
        headers = {}

        if user is not None or password is not None:
            headers["Authorization"] = "Basic " + base64.b64encode((str(user) + ":" + str(password)).encode()).decode()
        elif login:
            name, secret = rest_login()
            headers["Authorization"] = "Basic " + base64.b64encode((name + ":" + secret).encode()).decode()

        connection = _Connection(host, port, context=context, timeout=timeout)

        try:
            connection.request("GET", path, headers=headers)
            answer = connection.getresponse()
            return answer.status, answer.read().decode()
        finally:
            connection.close()
    except Fail:
        raise
    except Exception as error:
        return None, error.__class__.__name__ + ": " + str(error)


# One GET request in plain HTTP. The REST API speaks only HTTPS, so this must fail or be refused.
def rest_get_plain(host, port, path, timeout=5):
    try:
        connection = http.client.HTTPConnection(host, port, timeout=timeout)

        try:
            connection.request("GET", path)
            answer = connection.getresponse()
            return answer.status, answer.read().decode(errors="replace")
        finally:
            connection.close()
    except Exception as error:
        return None, error.__class__.__name__ + ": " + str(error)


# Which address and port to call: the ones given, or the server node's address from Terraform.
def rest_target(host=None, port=None):
    return host or node_addresses()[0], port or node_port()


# One GET request to the REST API of the server node (with the login), answered as (status code, parsed JSON).
# A failure to connect, or an answer that is not JSON, is an exception that says what to do.
def rest_json(path, host=None, port=None, timeout=30):
    host, port = rest_target(host, port)
    status, body = rest_get(host, port, path, timeout=timeout)

    if status is None:
        raise Fail("the REST API did not answer: " + body + "\n" + ADDRESS_HINT)

    try:
        return status, json.loads(body)
    except ValueError:
        raise Fail("the REST API answered HTTP " + str(status) + " to " + path + " with something that is not JSON: " + body.strip()[:200])


# ---------------------------------------------------------------- one tagged log line through the pipeline

def new_trace_id():
    return "smoke-" + datetime.now(timezone.utc).strftime("%m%d-%H%M%S") + "-" + secrets.token_hex(2)


def job_log():
    return kubectl("-n", NS, "logs", "job/" + JOB, "-c", "producer")[1]


# The programs write one JSON object per line (time, level, service, msg and more fields). Look in a log text for the line whose "msg" is
# this one and whose "trace_id" is this one. Returns (the line as it was printed, the parsed record), or (None, None). A prefix that
# kubectl logs --prefix puts in front of the line ("[pod/NAME/CONTAINER] ") is skipped.
def find_json_line(text, msg, trace):
    for line in text.splitlines():
        start = line.find("{")

        if start < 0:
            continue

        try:
            record = json.loads(line[start:])
        except ValueError:
            continue

        if isinstance(record, dict) and record.get("msg") == msg and record.get("trace_id") == trace:
            return line.strip(), record

    return None, None


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


# Create the Job that sends one log line carrying this trace id, wait until it has finished, and return where Kafka stored
# the message: {"topic", "partition", "offset", "log", "seconds"}.
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
    line, record = find_json_line(log, "sent", trace)

    if record is None:
        raise Fail("the producer finished, but its log has no 'sent' line with trace_id " + trace + ":\n" + log.strip())

    return {"topic": record["topic"], "partition": int(record["partition"]), "offset": int(record["offset"]), "log": line, "seconds": time.monotonic() - start}


# Run a command inside the Kafka pod. Its command line tools are in the image. A small memory limit keeps them from
# competing with the broker, which was started with its own, larger heap setting.
def kafka_exec(command, timeout=90):
    return kubectl("-n", NS, "exec", "kafka-0", "-c", "kafka", "--", "sh", "-c", 'export KAFKA_HEAP_OPTS="-Xms64m -Xmx128m"; ' + command, timeout=timeout)


# Read exactly one message back from the topic: the one at this partition and offset.
def kafka_read(topic, partition, offset):
    code, out, err = kafka_exec("kafka-console-consumer --bootstrap-server " + KAFKA_ADMIN + " --topic " + topic + " --partition " + str(partition) + " --offset " + str(offset) + " --max-messages 1 --timeout-ms 20000")

    for line in out.splitlines():
        if line.startswith("{"):
            try:
                return json.loads(line)
            except ValueError:
                break

    raise Fail("Kafka returned no message at partition " + str(partition) + ", offset " + str(offset) + " of topic " + topic + ".\nexit code " + str(code) + "\n" + (err.strip() or out.strip() or "no output")[-600:])


# {partition: number of the next offset}; the sum is the number of messages in the topic. None when the tool is not in the image.
def kafka_end_offsets(topic):
    for command in ("kafka-get-offsets --bootstrap-server " + KAFKA_ADMIN + " --topic " + topic,
                    "kafka-run-class kafka.tools.GetOffsetShell --bootstrap-server " + KAFKA_ADMIN + " --topic " + topic + " --time -1"):
        code, out, err = kafka_exec(command)
        found = re.findall(r"^" + re.escape(topic) + r":(\d+):(\d+)\s*$", out, flags=re.M)

        if code == 0 and found:
            return {int(p): int(o) for p, o in found}

    return None


# (number of partitions, replication factor) of the topic, or None.
def kafka_topic_shape(topic):
    code, out, err = kafka_exec("kafka-topics --bootstrap-server " + KAFKA_ADMIN + " --describe --topic " + topic)
    partitions, replicas = re.search(r"PartitionCount:\s*(\d+)", out), re.search(r"ReplicationFactor:\s*(\d+)", out)

    return (int(partitions.group(1)), int(replicas.group(1))) if partitions and replicas else None


# [(partition, current offset, end offset, lag)] of the consumer group, or None. A number is None where Kafka printed "-".
def kafka_group_lag(group):
    code, out, err = kafka_exec("kafka-consumer-groups --bootstrap-server " + KAFKA_ADMIN + " --describe --group " + group)
    rows = []

    for line in out.splitlines():
        t = line.split()

        if len(t) >= 6 and t[0] == group and t[2].isdigit():
            rows.append((int(t[2]),) + tuple(int(x) if x.isdigit() else None for x in t[3:6]))

    return rows or None


# The processor's log line for this trace id, as printed: the "stored" line with trace_id, topic, partition and offset (or None).
def processor_log_line(trace, since="15m"):
    selector = "app.kubernetes.io/name=processor," + CHAIN
    code, out, err = kubectl("-n", NS, "logs", "-l", selector, "-c", "processor", "--since=" + since, "--tail=-1", "--prefix", timeout=60)

    return find_json_line(out, "stored", trace)[0]


# Run a mongosh script inside the MongoDB pod, over TLS, as the administrator, and return the last line of output that starts with "{"
# as JSON. The user name and password are read from the mounted Secret by the shell inside the pod. They never reach this computer.
def mongosh_json(script, timeout=90):
    shell = ('mongosh --quiet --tls --tlsCAFile /etc/mongo-tls/ca.pem --host localhost -u "$(cat /etc/secrets/mongo-root/username)" '
             '-p "$(cat /etc/secrets/mongo-root/password)" --authenticationDatabase admin --eval "$1"')
    code, out, err = kubectl("-n", NS, "exec", "mongo-0", "-c", "mongo", "--", "sh", "-c", shell, "sh", script, timeout=timeout)

    for line in reversed(out.splitlines()):
        if line.startswith("{"):
            return json.loads(line)

    raise Fail("mongosh in mongo-0 gave no answer.\nexit code " + str(code) + "\n" + (err.strip() or out.strip() or "no output")[-600:])


# Ask MongoDB, from inside its own pod, for the stored line with this trace id: {"total", "found", "doc"} (total = lines stored in all).
def mongo_lookup(trace):
    database, collection = config_value("pipeline-config", "MONGO_DB"), config_value("pipeline-config", "MONGO_COLLECTION")
    script = ('const c = db.getSiblingDB("' + database + '").getCollection("' + collection + '"); '
              'print(JSON.stringify({total: Number(c.estimatedDocumentCount()), found: Number(c.countDocuments({trace_id: "' + trace + '"})), '
              'doc: c.findOne({trace_id: "' + trace + '"}, {_id: 0})}))')

    return mongosh_json(script)


# A short view of a record (a Kafka message or a stored document): the fields that tell a reader what it is, long texts cut, then a count
# of the others.
def trimmed(event, keep=("trace_id", "kind", "run", "smoke", "template_id", "truth", "alert", "keyword", "llm", "line", "content")):
    short = {}

    for k in keep:
        if k in event:
            value = event[k]
            short[k] = value[:70] + "..." if isinstance(value, str) and len(value) > 70 else value

    short["..."] = str(len(event) - len(short)) + " more fields"

    return short


# ---------------------------------------------------------------- the monitoring tools, asked from inside their own pods
# Prometheus, Loki and Grafana answer only on their own addresses inside the cluster, and the network rules admit only each other (and nothing
# from outside). "kubectl exec" runs a command inside the pod, so the question is asked from the pod itself, on localhost, which no network rule
# touches. The images of all three contain wget (BusyBox), which is all that is needed. Nothing is changed by any of these calls.

# Run a command in a pod of a Deployment or DaemonSet, for example exec_in("deployment/prometheus", "wget", ...).
def exec_in(target, *command, timeout=60):
    return kubectl("-n", NS, "exec", target, "--", *command, timeout=timeout)


# GET a path on localhost:<port> inside the pod of <target>. Returns the body; a failure is an exception that says which pod and path.
def local_get(target, port, path, timeout=60):
    code, out, err = exec_in(target, "wget", "-qO-", "http://localhost:" + str(port) + path, timeout=timeout)

    if code != 0:
        raise Fail("asking " + target + " for " + path.split("?")[0] + " did not work (exit code " + str(code) + "): " + (err.strip() or out.strip() or "no answer") + ". Is the pod running?  make status")

    return out


# An instant PromQL query. Returns the list of result series: [{"metric": {...}, "value": [time, "number"]}, ...] (empty when nothing matches).
def prom_query(expression):
    body = local_get("deployment/prometheus", 9090, "/api/v1/query?query=" + urllib.parse.quote(expression, safe=""))
    answer = json.loads(body)

    if answer.get("status") != "success":
        raise Fail("Prometheus did not accept the query " + expression + ": " + str(answer.get("error", answer))[:300])

    return answer["data"]["result"]


# The number that an aggregate query such as sum(...) returns, or None when the query returns no series. "NaN" is returned as None too.
def prom_number(expression):
    result = prom_query(expression)

    if not result:
        return None

    value = float(result[0]["value"][1])

    return None if value != value else value


# GET a path on Grafana, logged in as the administrator. The login is read from the mounted Secret by the shell inside the pod, so the
# password never reaches this computer and is never printed. Only fixed paths are passed in.
GRAFANA_GET = (r'''auth="$(printf '%s:%s' "$(cat /etc/secrets/grafana/username)" "$(cat /etc/secrets/grafana/password)" | base64 | tr -d '\n')"; '''
               r'''wget -qO- --header "Authorization: Basic $auth" "http://localhost:3000@PATH@"''')


def grafana_get(path):
    code, out, err = exec_in("deployment/grafana", "sh", "-c", GRAFANA_GET.replace("@PATH@", path))

    if code != 0:
        raise Fail("asking Grafana (logged in) for " + path.split("?")[0] + " did not work (exit code " + str(code) + "): " + (err.strip() or out.strip() or "no answer"))

    return out


# The log lines in Loki that match a LogQL query, oldest first: a list of (time in nanoseconds, labels, line).
def loki_lines(query, minutes=60, limit=500):
    end = int(time.time() * 1e9)
    start = end - minutes * 60 * 10**9
    path = "/loki/api/v1/query_range?query=" + urllib.parse.quote(query, safe="") + "&start=" + str(start) + "&end=" + str(end) + "&limit=" + str(limit) + "&direction=forward"
    answer = json.loads(local_get("deployment/loki", 3100, path))

    if answer.get("status") != "success":
        raise Fail("Loki did not accept the query " + query + ": " + str(answer)[:300])

    lines = [(int(ts), stream["stream"], line) for stream in answer["data"]["result"] for ts, line in stream["values"]]

    return sorted(lines, key=lambda x: x[0])
