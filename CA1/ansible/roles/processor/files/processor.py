#!/usr/bin/env python3

# This program reads network flow messages from Kafka and saves each flow into MongoDB.

import json
import os

from kafka import KafkaConsumer
from pymongo import MongoClient


# Get the Kafka broker address from an environment variable.
# The default value is only used if KAFKA_BROKER is not provided.
BROKER = os.environ.get("KAFKA_BROKER", "172.31.21.128:9092")

# Kafka topic that contains the network flow data.
TOPIC = "network-flows"


# Get the MongoDB connection information from environment variables.
# Ansible will provide these values when the processor container is started.
MONGO_HOST = os.environ.get("MONGO_HOST", "172.31.24.111")
MONGO_PORT = 27017

# Get the MongoDB username and password from environment variables.
# These values will come from the encrypted Ansible Vault.
MONGO_USER = os.environ.get("MONGO_USER", "")
MONGO_PASSWORD = os.environ.get("MONGO_PASSWORD", "")


# MongoDB database and collection used to store the flows.
DB_NAME = "ca0"
COLLECTION_NAME = "flows"


# Connect to Kafka and start reading messages from the network-flows topic.
consumer = KafkaConsumer(
    TOPIC,
    bootstrap_servers=BROKER,
    value_deserializer=lambda v: json.loads(v.decode("utf-8")),
    auto_offset_reset="earliest",
    group_id="processor-group",
)


# Connect to MongoDB using authentication when a username and password are provided.
#
# The else block keeps the old CA0 behavior available for local testing without MongoDB authentication.
if MONGO_USER and MONGO_PASSWORD:
    client = MongoClient(
        MONGO_HOST,
        MONGO_PORT,
        username=MONGO_USER,
        password=MONGO_PASSWORD,
    )
else:
    client = MongoClient(MONGO_HOST, MONGO_PORT)


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

    # Print an alert when the flow is not BENIGN.
    label = row.get("Label", "?")

    if label != "BENIGN":
        print(f"[ALERT] {label} flow inserted (processed so far: {count})")

    # For normal traffic, print progress every 500 rows instead of printing every single record.
    elif count % 500 == 0:
        print(f"processed {count} rows so far...")
