# This program replays the network flow dataset into Kafka, or sends one tagged test event.
#
# It is written to run in a Kubernetes pod. The default behaviour is simple: replay the file once and stop.
#   1. REPLAY_FOREVER=true keeps replaying the file until Kubernetes stops the pod. The scaling test uses this
#      to create sustained load, so the Horizontal Pod Autoscaler has something to react to.
#   2. SEND_DELAY_SECONDS and MAX_ROWS are optional tuning settings (they change how it behaves, not where it connects).
#   3. It stops cleanly when Kubernetes asks it to (SIGTERM).

import argparse
import csv
import json
import os
import signal
import sys
import time


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

# Optional tuning settings. They only change HOW fast and HOW long the producer sends, never WHERE,
# so they have defaults: one pass over the file, as fast as possible, every row.
REPLAY_FOREVER = os.environ.get("REPLAY_FOREVER", "false").lower() == "true"
DELAY_SECONDS = float(os.environ.get("SEND_DELAY_SECONDS", "0"))
LIMIT = int(os.environ["MAX_ROWS"]) if os.environ.get("MAX_ROWS") else None


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


# Connect to Kafka
producer = KafkaProducer(
    bootstrap_servers=BROKER,

    # Turn each Python row into JSON text, then turn that text into bytes because Kafka sends bytes
    value_serializer=lambda v: json.dumps(v).encode("utf-8"),
)


# Read the whole CSV into memory once (it has only a few thousand rows).
def load_rows():
    with open(CSV_FILE, newline="") as f:
        # Read each CSV row as a Python dictionary
        reader = csv.DictReader(f)

        # Some CICIDS2017 column names contain extra spaces. Remove those spaces so " Label" becomes "Label".
        reader.fieldnames = [name.strip() for name in reader.fieldnames]

        return list(reader)


def main():
    rows = load_rows()

    # Keep track of how many rows we have sent
    sent = 0

    started = time.time()
    last_report = started
    sent_at_last_report = 0

    while running:
        # Go through the CSV one row at a time
        for row in rows:
            if not running:
                break

            # Send this network-flow row to our Kafka topic
            producer.send(TOPIC, value=row)

            sent += 1

            if not REPLAY_FOREVER and sent % 500 == 0:
                print(f"sent {sent} rows...", flush=True)

            # Pause between rows if a delay was set
            if DELAY_SECONDS:
                time.sleep(DELAY_SECONDS)

            # If we set a limit, stop after sending that many rows
            if LIMIT and sent >= LIMIT:
                break

            # In replay-forever mode, report the sending rate every 5 seconds.
            # The scaling test reads these lines to compare the rate before and after scaling.
            if REPLAY_FOREVER:
                now = time.time()

                if now - last_report >= 5:
                    rate = (sent - sent_at_last_report) / (now - last_report)
                    print(f"sent {sent} rows total, {rate:.0f} msg/s", flush=True)
                    last_report = now
                    sent_at_last_report = sent

        # One pass is enough unless we were asked to keep going.
        if not REPLAY_FOREVER or (LIMIT and sent >= LIMIT):
            break

    # Wait until Kafka has finished sending any messages that are still waiting to be sent
    producer.flush()

    # Show how many rows were sent
    print(f"done - sent {sent} rows total", flush=True)


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

    print(f"sent trace_id={trace_id} topic={stored.topic} partition={stored.partition} offset={stored.offset}", flush=True)


# Run when we start this file directly.
# With --trace-id it sends one tagged test event. Without it, it replays the whole dataset.
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Replay the dataset into Kafka, or send one tagged test event.")
    parser.add_argument("--trace-id", help="send a single test event carrying this trace id, instead of replaying the dataset")
    args = parser.parse_args()

    if args.trace_id:
        send_one_event(args.trace_id)
    else:
        main()
