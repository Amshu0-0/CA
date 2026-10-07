#!/usr/bin/env python3

import csv
import json
import os
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
# Ansible provides these in an environment file on the producer VM.
# The values come from config.yml and the Ansible inventory.

# Private IP and port of the Kafka broker, and the Kafka topic where the network flow data is sent.
BROKER = require("KAFKA_BROKER")
TOPIC = require("KAFKA_TOPIC")

# The CSV file to replay. Its name comes from config.yml.
CSV_FILE = require("DATASET_FILE")

# This slows the replay down so we can see the data moving during the demo
DELAY_SECONDS = 0

# Send every row in the csv, no limit
LIMIT = None


# This library is imported after the settings check on purpose,
# so a missing setting is always the first thing reported.
from kafka import KafkaProducer


# Connect to Kafka
producer = KafkaProducer(
    bootstrap_servers=BROKER,

    # Turn each Python row into JSON text, then turn that text into bytes because Kafka sends bytes
    value_serializer=lambda v: json.dumps(v).encode("utf-8"),
)

def main():

    with open(CSV_FILE, newline="") as f:

        # Read each CSV row as a Python dictionary
        reader = csv.DictReader(f)

        # Some CICIDS2017 column names contain extra spaces. Remove those spaces so " Label" becomes "Label".
        reader.fieldnames = [name.strip() for name in reader.fieldnames]

        # Keep track of how many rows we have sent
        sent = 0

        # Go through the CSV one row at a time
        for row in reader:

            # Send this network-flow row to our Kafka topic
            producer.send(TOPIC, value=row)

            sent += 1

            if sent % 500 == 0:
                print(f"sent {sent} rows...")

            # Pause between rows if a delay was set
            if DELAY_SECONDS:
                time.sleep(DELAY_SECONDS)

            # If we set a LIMIT, stop after sending that many rows
            if LIMIT and sent >= LIMIT:
                break

    # Wait until Kafka has finished sending any messages that are still waiting to be sent
    producer.flush()

    # Show how many rows were sent
    print(f"done - sent {sent} rows total")


# Run main() when we start this file directly
if __name__ == "__main__":
    main()
