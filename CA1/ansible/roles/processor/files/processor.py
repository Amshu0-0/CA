#!/usr/bin/env python3

# This program reads network flow messages from Kafka and saves each flow into MongoDB.

import json
import os
import sys


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
# Ansible provides all of these when it starts the processor container.
# The values come from config.yml, the Ansible inventory, and the encrypted Ansible Vault.

# Kafka broker address and the topic that contains the network flow data.
BROKER = require("KAFKA_BROKER")
TOPIC = require("KAFKA_TOPIC")

# MongoDB address, plus the database and collection used to store the flows.
MONGO_HOST = require("MONGO_HOST")
MONGO_PORT = int(require("MONGO_PORT"))
DB_NAME = require("MONGO_DB")
COLLECTION_NAME = require("MONGO_COLLECTION")

# MongoDB username and password. These come from the encrypted Ansible Vault.
MONGO_USER = require("MONGO_USER")
MONGO_PASSWORD = require("MONGO_PASSWORD")


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
    group_id="processor-group",
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


print("processor listening on topic:", TOPIC)

count = 0

# Keep reading messages from Kafka.
for message in consumer:
    row = message.value

    # Save the network flow into MongoDB.
    collection.insert_one(row)
    count += 1

    # If this event carries a trace_id, log it together with its place in Kafka.
    #
    # Real traffic from the dataset has no trace_id, so this stays silent for it.
    # The end-to-end smoke test tags one event with a unique trace_id and then looks for this line.
    # It proves the processor read that exact event from that exact Kafka offset and stored it.
    trace_id = row.get("trace_id")

    if trace_id:
        print(f"[TRACE] trace_id={trace_id} partition={message.partition} offset={message.offset} stored", flush=True)

    # Print an alert when the flow is not BENIGN.
    label = row.get("Label", "?")

    if label != "BENIGN":
        print(f"[ALERT] {label} flow inserted (processed so far: {count})")

    # For normal traffic, print progress every 500 rows instead of printing every single record.
    elif count % 500 == 0:
        print(f"processed {count} rows so far...")
