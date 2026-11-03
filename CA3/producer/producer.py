# This program replays the network flow dataset into Kafka, or sends one tagged test event.
#
# It is written to run in a Kubernetes pod. The default behaviour is simple: replay the file once and stop.
#   1. REPLAY_FOREVER=true keeps replaying the file until Kubernetes stops the pod. The scaling test uses this
#      to create sustained load, so the Horizontal Pod Autoscaler has something to react to.
#   2. SEND_DELAY_SECONDS, MAX_ROWS and FLUSH_EVERY_ROWS are optional tuning settings (they change how it behaves, not where it connects).
#   3. It stops cleanly when Kubernetes asks it to (SIGTERM).
#   4. It talks to Kafka over TLS and shows a client certificate (mutual TLS). Kafka refuses a client without a certificate that the
#      stack's own certificate authority signed. The paths of the three PEM files come from the ConfigMap pipeline-config.
#   5. PRODUCER_IDLE=true: connect to Kafka (which proves that the certificate works), send nothing, and wait. The load Deployment
#      that "make up" creates runs like this until the scaling test switches the load on (see producer/loadtest/).
#   6. Every log line is one JSON object (time, level, service, message and fields such as topic, partition, offset, trace_id).
#   7. /metrics on PRODUCER_METRICS_PORT (Prometheus reads it): rows sent, rows that Kafka did not accept, how long Kafka takes to
#      confirm a batch, and whether this pod is sending or idle.
#   8. If Kafka stops answering while the producer is sending (the resilience drill deletes the Kafka pod), the producer does not crash:
#      it counts and logs the rows that were not accepted, waits a moment and goes on, and it sends again as soon as Kafka is back.

import argparse
import csv
import json
import os
import signal
import sys
import time
from datetime import datetime, timezone

from prometheus_client import Counter, Gauge, Histogram, start_http_server

SERVICE = "producer"


# One log line = one JSON object on standard output. Extra fields are given as keyword arguments.
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


# Read every setting before connecting to anything, so a missing one is reported first.
# Kubernetes provides them from the ConfigMap pipeline-config.

# Address of the Kafka broker, and the Kafka topic where the network flow data is sent.
BROKER = require("KAFKA_BROKER")
TOPIC = require("KAFKA_TOPIC")

# The CSV file to replay. It is baked into the image.
CSV_FILE = require("DATASET_FILE")

# The TLS files: the certificate authority that signed Kafka's certificate, and this producer's own certificate and key.
# They are mounted from the Secret kafka-client-producer. There is deliberately no way to turn TLS off.
SSL_CA = require("KAFKA_SSL_CA_FILE")
SSL_CERT = require("KAFKA_SSL_CERT_FILE")
SSL_KEY = require("KAFKA_SSL_KEY_FILE")

# The port where Prometheus reads this pod's numbers (/metrics).
METRICS_PORT = int(require("PRODUCER_METRICS_PORT"))

# Optional tuning settings. They only change HOW fast and HOW long the producer sends, never WHERE,
# so they have defaults: one pass over the file, as fast as possible, every row.
REPLAY_FOREVER = os.environ.get("REPLAY_FOREVER", "false").lower() == "true"
DELAY_SECONDS = float(os.environ.get("SEND_DELAY_SECONDS", "0"))
LIMIT = int(os.environ["MAX_ROWS"]) if os.environ.get("MAX_ROWS") else None

# Replay-forever mode only: after this many rows, wait until Kafka has confirmed them (see the loop below).
FLUSH_EVERY = int(os.environ.get("FLUSH_EVERY_ROWS", "500"))

# Idle mode: stay connected, send nothing. Used by the load Deployment until the scaling test switches the load on.
IDLE = os.environ.get("PRODUCER_IDLE", "false").lower() == "true"


# ---------------------------------------------------------------- the numbers (served at /metrics)

SENT = Counter("producer_messages_sent_total", "Rows handed to Kafka by this producer.")
FAILED = Counter("producer_send_errors_total", "Rows that Kafka did not accept (Kafka unreachable or too slow).")
FLUSH_SECONDS = Histogram("producer_flush_seconds", "Seconds spent waiting for Kafka to confirm a batch of rows. It grows when Kafka is the slow part.",
                          buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30))
SENDING = Gauge("producer_sending", "1 while this pod replays the dataset, 0 while it is idle or finished.")
SENDING.set(0)

# Kubernetes sends SIGTERM to stop a pod. Finish the current row, flush, and leave.
running = True


def stop(signum, frame):
    global running
    running = False


signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)


# This library is imported after the settings check on purpose,
# so a missing setting is always the first thing reported.
from kafka import KafkaProducer
from kafka.errors import KafkaError


# Connect to Kafka over TLS. ssl_check_hostname makes the producer check that the certificate Kafka shows really names the host
# it connected to, so a pod that merely answers on that address cannot pass for Kafka.
producer = KafkaProducer(
    bootstrap_servers=BROKER,
    security_protocol="SSL",
    ssl_cafile=SSL_CA,
    ssl_certfile=SSL_CERT,
    ssl_keyfile=SSL_KEY,
    ssl_check_hostname=True,

    # Turn each Python row into JSON text, then turn that text into bytes because Kafka sends bytes
    value_serializer=lambda v: json.dumps(v).encode("utf-8"),
)


# Rows that Kafka refused or never confirmed. Kafka's library calls this function from its own thread for every row of a failed batch.
failures = {"rows": 0, "last": ""}


def row_failed(error):
    failures["rows"] += 1
    failures["last"] = error.__class__.__name__ + ": " + str(error)[:200]
    FAILED.inc()


# Read the whole CSV into memory once (it has only a few thousand rows).
def load_rows():
    with open(CSV_FILE, newline="") as f:
        # Read each CSV row as a Python dictionary
        reader = csv.DictReader(f)

        # Some CICIDS2017 column names contain extra spaces. Remove those spaces so " Label" becomes "Label".
        reader.fieldnames = [name.strip() for name in reader.fieldnames]

        return list(reader)


# Wait until Kafka has confirmed everything sent so far, and say so if some rows were not accepted.
# Returns how many rows were reported as failed since the last call.
def flush_and_report(reported):
    started = time.time()

    try:
        producer.flush(timeout=60)
    except KafkaError as error:
        log("error", "Kafka did not confirm the rows within 60 seconds", error=error.__class__.__name__, topic=TOPIC)
        time.sleep(2)

    FLUSH_SECONDS.observe(time.time() - started)

    if failures["rows"] > reported:
        log("error", "Kafka did not accept some rows", rows=failures["rows"] - reported, last_error=failures["last"], topic=TOPIC)
        time.sleep(1)

    return failures["rows"]


def main():
    rows = load_rows()

    # Keep track of how many rows we have sent
    sent = 0

    reported = 0
    started = time.time()
    last_report = started
    sent_at_last_report = 0

    # Prometheus reads the numbers from here. Only the long-running modes serve them (the one-shot test event does not),
    # and only after the connection to Kafka worked, so "target is up" means "this pod is really connected".
    start_http_server(METRICS_PORT)

    SENDING.set(1)
    log("info", "producer started", topic=TOPIC, rows_in_file=len(rows), replay_forever=REPLAY_FOREVER)

    while running:
        # Go through the CSV one row at a time
        for row in rows:
            if not running:
                break

            # Send this network-flow row to our Kafka topic. If Kafka cannot even be asked (no connection, no answer to the question
            # "who leads this topic?"), the library raises an error: count it, say so, wait and go on with the next row.
            try:
                producer.send(TOPIC, value=row).add_errback(row_failed)
            except KafkaError as error:
                row_failed(error)
                log("error", "send failed", error=error.__class__.__name__, topic=TOPIC)
                time.sleep(2)
                continue

            sent += 1
            SENT.inc()

            # producer.send() only puts the row into a queue in memory and returns at once, and this version of the Kafka library
            # has no limit on that queue. A loop that never waits therefore fills the memory of the pod in seconds: the scaling
            # test found exactly this (the pod was killed with OOMKilled after 13 seconds). So in replay-forever mode we wait for
            # Kafka's confirmation every FLUSH_EVERY rows. The queue stays small, and "msg/s" is the speed Kafka really accepts.
            if REPLAY_FOREVER and sent % FLUSH_EVERY == 0:
                reported = flush_and_report(reported)

            if not REPLAY_FOREVER and sent % 500 == 0:
                log("info", "progress", rows_sent=sent, topic=TOPIC)

            # Pause between rows if a delay was set
            if DELAY_SECONDS:
                time.sleep(DELAY_SECONDS)

            # If we set a limit, stop after sending that many rows
            if LIMIT and sent >= LIMIT:
                break

            # In replay-forever mode, log the sending rate every 5 seconds.
            if REPLAY_FOREVER:
                now = time.time()

                if now - last_report >= 5:
                    rate = (sent - sent_at_last_report) / (now - last_report)
                    log("info", "rate", rows_total=sent, rate_msg_s=round(rate), topic=TOPIC)
                    last_report = now
                    sent_at_last_report = sent

        # One pass is enough unless we were asked to keep going.
        if not REPLAY_FOREVER or (LIMIT and sent >= LIMIT):
            break

    # Wait until Kafka has finished sending any messages that are still waiting to be sent
    flush_and_report(reported)
    SENDING.set(0)

    # Show how many rows were sent
    log("info", "producer done", rows_sent=sent, topic=TOPIC)


# Send ONE event that carries a unique trace_id, then report exactly where Kafka stored it.
#
# This is used by the end-to-end smoke test.
# The event is a copy of the first row of the dataset, so it has the same fields as real traffic,
# with two changes: its Label is SMOKE-TEST, so it can never be mistaken for real data,
# and it carries the trace_id that the test will look for at every later step.
def send_one_event(trace_id):
    event = load_rows()[0]

    event["Label"] = "SMOKE-TEST"
    event["trace_id"] = trace_id

    # Waiting for the answer means Kafka has really stored the message.
    # The answer says which topic, partition, and offset it was stored at.
    stored = producer.send(TOPIC, value=event).get(timeout=15)
    SENT.inc()

    log("info", "sent", trace_id=trace_id, topic=stored.topic, partition=stored.partition, offset=stored.offset)


# Idle mode: the connection above already worked (the producer would have stopped with an error otherwise), so say so and wait.
def idle():
    start_http_server(METRICS_PORT)
    log("info", "idle: connected to Kafka over TLS, sending nothing. The scaling test switches the load on (producer/loadtest).", topic=TOPIC)

    while running:
        time.sleep(1)

    producer.close()
    log("info", "idle producer stopped")


# Run when we start this file directly.
# With --trace-id it sends one tagged test event. With PRODUCER_IDLE=true it waits. Otherwise it replays the whole dataset.
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Replay the dataset into Kafka, or send one tagged test event.")
    parser.add_argument("--trace-id", help="send a single test event carrying this trace id, instead of replaying the dataset")
    args = parser.parse_args()

    if args.trace_id:
        send_one_event(args.trace_id)
    elif IDLE:
        idle()
    else:
        main()
