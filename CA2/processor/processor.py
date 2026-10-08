# This program reads network flow messages from Kafka and saves each flow into MongoDB.
#
# It is written to run in a Kubernetes pod, which adds three things to the basic job:
#   1. The MongoDB credentials can come from files that Kubernetes mounts from a Secret
#      (MONGO_USER_FILE and MONGO_PASSWORD_FILE), so no password sits in a manifest or in an environment variable.
#   2. A small HTTP server answers /healthz, /readyz and /metrics. Kubernetes uses it for its probes,
#      and the processor's ClusterIP Service points at it.
#   3. The program stops cleanly when Kubernetes asks it to (SIGTERM), so no message is cut in half.

import json
import os
import signal
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


# Read one required setting from an environment variable.
#
# There is deliberately no default value. If a setting is missing, the program stops
# right away and names it, instead of quietly falling back to an old address or name.
def require(name):
    value = os.environ.get(name)

    if not value:
        sys.exit(f"missing required setting: {name}")

    return value


# Read one required credential.
#
# On Kubernetes the credential is a file (a mounted Secret) and NAME_FILE says where that file is.
# For a quick local run it can still be given directly in the NAME environment variable.
def require_secret(name):
    path = os.environ.get(name + "_FILE")

    if path:
        try:
            with open(path) as f:
                value = f.read().rstrip("\n")
        except OSError as error:
            sys.exit(f"cannot read {name}_FILE ({path}): {error}")

        if not value:
            sys.exit(f"{name}_FILE ({path}) is empty")

        return value

    value = os.environ.get(name)

    if not value:
        sys.exit(f"missing required setting: {name} (or {name}_FILE)")

    return value


# Read every setting before connecting to anything, so a missing one is reported first.
# Kubernetes provides them: the ConfigMaps pipeline-config and processor-config, and the Secret mongo-credentials.

# Kafka broker address, the topic that contains the network flow data, and this consumer's group name.
BROKER = require("KAFKA_BROKER")
TOPIC = require("KAFKA_TOPIC")
CONSUMER_GROUP = require("CONSUMER_GROUP")

# MongoDB address, plus the database and collection used to store the flows.
MONGO_HOST = require("MONGO_HOST")
MONGO_PORT = int(require("MONGO_PORT"))
DB_NAME = require("MONGO_DB")
COLLECTION_NAME = require("MONGO_COLLECTION")

# The port of the health and metrics server.
METRICS_PORT = int(require("METRICS_PORT"))

# MongoDB username and password, read from the mounted Secret.
MONGO_USER = require_secret("MONGO_USER")
MONGO_PASSWORD = require_secret("MONGO_PASSWORD")


# If the consumer loop has not checked Kafka for this many seconds, /healthz reports it as stuck
# and Kubernetes restarts the container.
STALL_SECONDS = 60


# The numbers shared between the consumer loop (which writes them) and the HTTP server thread (which reads them).
class Stats:
    def __init__(self):
        self.lock = threading.Lock()
        self.messages = 0
        self.alerts = 0
        self.last_message_time = 0.0
        self.last_poll_time = time.time()
        self.ready = False


stats = Stats()


# The text served at /metrics, in the Prometheus text format.
def render_metrics():
    with stats.lock:
        lines = [
            "# HELP processor_messages_total Network flows read from Kafka and stored in MongoDB.",
            "# TYPE processor_messages_total counter",
            f"processor_messages_total {stats.messages}",
            "# HELP processor_alerts_total Stored flows whose Label is not BENIGN.",
            "# TYPE processor_alerts_total counter",
            f"processor_alerts_total {stats.alerts}",
            "# HELP processor_last_message_timestamp_seconds Unix time of the last stored flow (0 if none yet).",
            "# TYPE processor_last_message_timestamp_seconds gauge",
            f"processor_last_message_timestamp_seconds {stats.last_message_time:.3f}",
            "# HELP processor_ready 1 when the processor is connected to Kafka and MongoDB.",
            "# TYPE processor_ready gauge",
            f"processor_ready {1 if stats.ready else 0}",
        ]

    return "\n".join(lines) + "\n"


# The three small pages Kubernetes and the metrics scraper ask for.
class Handler(BaseHTTPRequestHandler):
    def reply(self, code, body, content_type="text/plain; charset=utf-8"):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        # Liveness: is the consumer loop still turning? If not, Kubernetes restarts the container.
        if self.path == "/healthz":
            stalled = time.time() - stats.last_poll_time > STALL_SECONDS
            self.reply(503 if stalled else 200, "consumer loop stalled\n" if stalled else "ok\n")

        # Readiness: is it connected to Kafka and MongoDB? If not, Kubernetes keeps it out of the Service.
        elif self.path == "/readyz":
            self.reply(200 if stats.ready else 503, "ready\n" if stats.ready else "not ready\n")

        # Metrics: counters for the scaling and observability evidence.
        elif self.path == "/metrics":
            self.reply(200, render_metrics(), "text/plain; version=0.0.4; charset=utf-8")

        else:
            self.reply(404, "not found\n")

    # Do not print one log line per probe.
    def log_message(self, format, *args):
        pass


# Start the HTTP server before connecting to Kafka and MongoDB, so the probes work while it connects.
server = ThreadingHTTPServer(("0.0.0.0", METRICS_PORT), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()


# Kubernetes sends SIGTERM to stop a pod. Finish the current batch and then leave the loop.
running = True


def stop(signum, frame):
    global running
    running = False


signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)


# These libraries are imported after the settings check on purpose,
# so a missing setting is always the first thing reported.
from kafka import KafkaConsumer
from pymongo import MongoClient


# Connect to Kafka and start reading messages from the configured topic.
consumer = KafkaConsumer(
    TOPIC,
    bootstrap_servers=BROKER,
    value_deserializer=lambda v: json.loads(v.decode("utf-8")),
    auto_offset_reset="earliest",
    group_id=CONSUMER_GROUP,
)


# Connect to MongoDB using the username and password.
client = MongoClient(
    MONGO_HOST,
    MONGO_PORT,
    username=MONGO_USER,
    password=MONGO_PASSWORD,
)


# Select the database and collection where the network flow records will be stored.
collection = client[DB_NAME][COLLECTION_NAME]

# Ask MongoDB to answer once, so wrong credentials or a missing database stop the program now
# (Kubernetes then restarts it) instead of on the first message.
client.admin.command("ping")


print("processor listening on topic:", TOPIC, flush=True)

stats.ready = True
count = 0

# Keep reading messages from Kafka. poll() waits up to one second for new messages,
# which also lets the loop notice that it was asked to stop.
while running:
    batches = consumer.poll(timeout_ms=1000, max_records=500)
    stats.last_poll_time = time.time()

    for records in batches.values():
        for message in records:
            row = message.value

            # Save the network flow into MongoDB.
            collection.insert_one(row)
            count += 1

            label = row.get("Label", "?")

            with stats.lock:
                stats.messages += 1
                stats.last_message_time = time.time()

                if label != "BENIGN":
                    stats.alerts += 1

            # If this event carries a trace_id, log it together with its place in Kafka.
            #
            # Real traffic from the dataset has no trace_id, so this stays silent for it.
            # The end-to-end smoke test tags one event with a unique trace_id and then looks for this line.
            # It proves the processor read that exact event from that exact Kafka offset and stored it.
            trace_id = row.get("trace_id")

            if trace_id:
                print(f"[TRACE] trace_id={trace_id} partition={message.partition} offset={message.offset} stored", flush=True)

            # Print an alert when the flow is not BENIGN.
            if label != "BENIGN":
                print(f"[ALERT] {label} flow inserted (processed so far: {count})", flush=True)

            # For normal traffic, print progress every 500 rows instead of printing every single record.
            elif count % 500 == 0:
                print(f"processed {count} rows so far...", flush=True)


# Leave cleanly: commit the Kafka offsets and close both connections.
stats.ready = False
consumer.close()
client.close()
print("processor stopped", flush=True)
