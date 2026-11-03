# This program reads network flow messages from Kafka and saves each flow into MongoDB.
#
# It is written to run in a Kubernetes pod, which adds these things to the basic job:
#   1. The MongoDB credentials can come from files that Kubernetes mounts from a Secret
#      (MONGO_USER_FILE and MONGO_PASSWORD_FILE), so no password sits in a manifest or in an environment variable.
#   2. A small HTTP server answers /healthz, /readyz and /metrics. Kubernetes uses the first two for its probes,
#      and Prometheus reads the third one every few seconds (see monitoring/prometheus.yaml).
#   3. The program stops cleanly when Kubernetes asks it to (SIGTERM), so no message is cut in half.
#   4. Both connections are encrypted. Kafka is reached over TLS with a client certificate (mutual TLS), MongoDB over TLS with
#      a user that may only read and write the database ca3 (not the administrator). There is no way to turn TLS off.
#   5. The messages of one poll are stored with one insert_many call instead of one insert_one call each. One round trip to MongoDB
#      per message was what held the pipeline back in the first scaling test: the processors were busy, but waiting for MongoDB.
#   6. Every log line is one JSON object (time, level, service, message and fields such as topic, partition, offset, trace_id).
#      The log collector (Grafana Alloy) turns "level" and "service" into labels, and Loki can search every other field.
#   7. The numbers that matter are measured here: flows stored, alerts, how long MongoDB takes per batch, and how old a flow is when
#      it is stored (Kafka stamps every message when the producer sends it, so "now minus that stamp" is the time the flow spent in the
#      pipeline). The last one is the number that the service goals in the README are about.
#
# If the batch cannot be stored (MongoDB down, for example) the program logs the error and stops WITHOUT committing the batch's Kafka
# offsets. Kubernetes restarts the container, and Kafka hands the same messages out again: nothing is lost, and the restart is visible.

import json
import os
import signal
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

SERVICE = "processor"


# One log line = one JSON object on standard output. Extra fields are given as keyword arguments:
#   log("info", "stored", trace_id="smoke-1", partition=2, offset=17)
def log(level, message, **fields):
    record = {"ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"), "level": level, "service": SERVICE, "msg": message}
    record.update(fields)
    print(json.dumps(record, separators=(",", ":"), default=str), flush=True)


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
# Kubernetes provides them: the ConfigMaps pipeline-config and processor-config, and the mounted Secrets.

# Kafka broker address, the topic that contains the network flow data, and this consumer's group name.
BROKER = require("KAFKA_BROKER")
TOPIC = require("KAFKA_TOPIC")
CONSUMER_GROUP = require("CONSUMER_GROUP")

# The TLS files for Kafka: the certificate authority that signed Kafka's certificate, and this processor's own certificate and key
# (mounted from the Secret kafka-client-processor). For MongoDB only the certificate authority is needed.
SSL_CA = require("KAFKA_SSL_CA_FILE")
SSL_CERT = require("KAFKA_SSL_CERT_FILE")
SSL_KEY = require("KAFKA_SSL_KEY_FILE")
MONGO_TLS_CA = require("MONGO_TLS_CA_FILE")

# MongoDB address, plus the database and collection used to store the flows.
MONGO_HOST = require("MONGO_HOST")
MONGO_PORT = int(require("MONGO_PORT"))
DB_NAME = require("MONGO_DB")
COLLECTION_NAME = require("MONGO_COLLECTION")

# The port of the health and metrics server.
METRICS_PORT = int(require("METRICS_PORT"))

# MongoDB username and password, read from the mounted Secret mongo-processor (a user with read and write rights on ca3 only).
MONGO_USER = require_secret("MONGO_USER")
MONGO_PASSWORD = require_secret("MONGO_PASSWORD")


# If the consumer loop has not checked Kafka for this many seconds, /healthz reports it as stuck
# and Kubernetes restarts the container.
STALL_SECONDS = 60


# ---------------------------------------------------------------- the numbers (served at /metrics)

# Counters only go up. Prometheus turns them into "per second" with rate().
MESSAGES = Counter("processor_messages_total", "Network flows read from Kafka and stored in MongoDB.")
ALERTS = Counter("processor_alerts_total", "Stored flows whose Label is not BENIGN.")
BATCHES = Counter("processor_insert_batches_total", "insert_many calls made to MongoDB.")

# A histogram counts how many observations fell into each time bucket, so Prometheus can work out percentiles (the 95th, for example).
INSERT_SECONDS = Histogram("processor_insert_batch_seconds", "Seconds that one insert_many call took: the time spent waiting for MongoDB.",
                           buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10))
END_TO_END = Histogram("processor_end_to_end_seconds", "Seconds from the producer's send (the Kafka timestamp of the message) to the moment the flow is stored in MongoDB.",
                       buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300, 600))

READY = Gauge("processor_ready", "1 when the processor is connected to Kafka and MongoDB.")
LAST_MESSAGE = Gauge("processor_last_message_timestamp_seconds", "Unix time of the last stored flow (0 if none yet).")


# The two facts that the probes need. The consumer loop writes them, the HTTP server thread reads them.
class State:
    def __init__(self):
        self.ready = False
        self.last_poll_time = time.time()


state = State()


def set_ready(value):
    state.ready = value
    READY.set(1 if value else 0)


set_ready(False)
LAST_MESSAGE.set(0)


# The three small pages Kubernetes and Prometheus ask for.
class Handler(BaseHTTPRequestHandler):
    def reply(self, code, body, content_type="text/plain; charset=utf-8"):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        # Liveness: is the consumer loop still turning? If not, Kubernetes restarts the container.
        if self.path == "/healthz":
            stalled = time.time() - state.last_poll_time > STALL_SECONDS
            self.reply(503 if stalled else 200, "consumer loop stalled\n" if stalled else "ok\n")

        # Readiness: is it connected to Kafka and MongoDB? If not, Kubernetes keeps it out of the Service.
        elif self.path == "/readyz":
            self.reply(200 if state.ready else 503, "ready\n" if state.ready else "not ready\n")

        # Metrics: the Prometheus text format (the numbers above, plus the process's CPU time, memory and start time).
        elif self.path == "/metrics":
            self.reply(200, generate_latest(), CONTENT_TYPE_LATEST)

        else:
            self.reply(404, "not found\n")

    # Do not log one line per probe or per scrape.
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


# Connect to Kafka over TLS and start reading messages from the configured topic. ssl_check_hostname makes the processor check that
# the certificate Kafka shows names the host it connected to.
consumer = KafkaConsumer(
    TOPIC,
    bootstrap_servers=BROKER,
    security_protocol="SSL",
    ssl_cafile=SSL_CA,
    ssl_certfile=SSL_CERT,
    ssl_keyfile=SSL_KEY,
    ssl_check_hostname=True,
    value_deserializer=lambda v: json.loads(v.decode("utf-8")),
    auto_offset_reset="earliest",
    group_id=CONSUMER_GROUP,

    # If a processor dies without saying goodbye (killed, out of memory, node lost), Kafka waits this long before it hands its partitions
    # to another processor, and the lag grows meanwhile. The library's default is 45 seconds; 10 seconds keeps the lag short after such
    # a failure. A processor that is stopped normally (SIGTERM) says goodbye, and the hand-over is immediate.
    session_timeout_ms=10000,
)


# Connect to MongoDB over TLS using the username and password. pymongo checks the server's certificate against the CA file,
# and checks that the certificate names the host ("mongo").
client = MongoClient(
    MONGO_HOST,
    MONGO_PORT,
    username=MONGO_USER,
    password=MONGO_PASSWORD,
    tls=True,
    tlsCAFile=MONGO_TLS_CA,
)


# Select the database and collection where the network flow records will be stored.
collection = client[DB_NAME][COLLECTION_NAME]

# Ask MongoDB to answer once, so wrong credentials or a missing database stop the program now
# (Kubernetes then restarts it) instead of on the first message.
client.admin.command("ping")


log("info", "processor listening", topic=TOPIC, consumer_group=CONSUMER_GROUP, kafka=BROKER, mongo=f"{MONGO_HOST}:{MONGO_PORT}", database=DB_NAME, collection=COLLECTION_NAME)

set_ready(True)
count = 0
logged = 0
alerts_total = 0


# Keep reading messages from Kafka. poll() waits up to one second for new messages,
# which also lets the loop notice that it was asked to stop.
try:
    while running:
        batches = consumer.poll(timeout_ms=1000, max_records=500)
        state.last_poll_time = time.time()

        messages = [message for records in batches.values() for message in records]

        if not messages:
            continue

        # Save the whole batch in one call. ordered=False lets MongoDB keep going if one document is refused.
        # If MongoDB cannot be reached the call raises an error: the handler below logs it and stops WITHOUT committing the offsets of
        # this batch, so Kafka hands the messages out again after Kubernetes has restarted the container.
        started = time.time()
        collection.insert_many([message.value for message in messages], ordered=False)
        stored_at = time.time()

        BATCHES.inc()
        INSERT_SECONDS.observe(stored_at - started)

        new_alerts = 0

        for message in messages:
            row = message.value
            label = row.get("Label", "?")

            # How long this flow was on its way: Kafka stamped it when the producer sent it (milliseconds since 1970).
            if message.timestamp and message.timestamp > 0:
                END_TO_END.observe(max(0.0, stored_at - message.timestamp / 1000.0))

            # Log an alert when the flow is not BENIGN. Only the first ten are logged one by one: at thousands of flows per second a
            # log line for every alert costs more CPU than storing the flow, so after that the progress line below carries the alert count.
            if label != "BENIGN":
                new_alerts += 1

                if alerts_total + new_alerts <= 10:
                    log("warning", "alert flow stored", topic=message.topic, label=label, alerts_so_far=alerts_total + new_alerts)

            # If this event carries a trace_id, log it together with its place in Kafka.
            #
            # Real traffic from the dataset has no trace_id, so this stays silent for it.
            # The end-to-end smoke test tags one event with a unique trace_id and then looks for this line.
            # It proves the processor read that exact event from that exact Kafka offset and stored it.
            trace_id = row.get("trace_id")

            if trace_id:
                log("info", "stored", trace_id=trace_id, topic=message.topic, partition=message.partition, offset=message.offset)

        count += len(messages)
        alerts_total += new_alerts

        MESSAGES.inc(len(messages))
        ALERTS.inc(new_alerts)
        LAST_MESSAGE.set(stored_at)

        # Log a progress line about every 5,000 rows instead of one line per record.
        if count - logged >= 5000:
            log("info", "progress", rows=count, alerts=alerts_total, topic=TOPIC)
            logged = count

except Exception as error:
    set_ready(False)
    log("error", "the batch was not stored: the processor stops and Kubernetes restarts it; Kafka hands the batch out again",
        error=error.__class__.__name__, detail=str(error)[:300], topic=TOPIC)

    # Do not commit the offsets of the batch that failed: it must be read again.
    try:
        consumer.close(autocommit=False)
    except Exception:
        pass

    sys.exit(1)


# Leave cleanly: commit the Kafka offsets and close both connections.
set_ready(False)
consumer.close()
client.close()
log("info", "processor stopped", rows=count)
