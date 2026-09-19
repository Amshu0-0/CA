#!/usr/bin/env python3

import json
import os
from kafka import KafkaConsumer
from pymongo import MongoClient


# Kafka port
BROKER = os.environ.get("KAFKA_BROKER", "172.31.21.128:9092")

# The Kafka topic to read message from
TOPIC = "network-flows"

# MongoDB port
MONGO_HOST = os.environ.get("MONGO_HOST", "172.31.24.111")
MONGO_PORT = 27017

# Save the data in MongoDB
DB_NAME = "ca0"
COLLECTION_NAME = "flows"


# Connect to Kafka and read messages from the network-flows topic
consumer = KafkaConsumer(
    TOPIC,

    # Address of our Kafka VM
    bootstrap_servers=BROKER,

    # Kafka gives us bytes, Turn those bytes back into JSON/Python data.
    value_deserializer=lambda v: json.loads(v.decode("utf-8")),

    # If this consumer has never read this topic before,start with the oldest messages still in Kafka.
    auto_offset_reset="earliest",

    # Give this consumer a group name so Kafka can keep track of what this group has already read.
    group_id="processor-group",
)




# Connect to MongoDB
client = MongoClient(MONGO_HOST, MONGO_PORT)

# Use the "ca0" database and the "flows" collection
collection = client[DB_NAME][COLLECTION_NAME]


print("processor listening on topic:", TOPIC)

# Keep track of how many rows we process
count = 0


# Keep waiting for messages from Kafka.
# Every time Kafka gives us a message, this loop runs.
for message in consumer:

    # Get the actual network-flow data from the Kafka message
    row = message.value

    # Save that network-flow row in MongoDB
    collection.insert_one(row)

    count += 1

    # Get the CICIDS2017 label from this row, Use "?" if the row doesn't have a Label.
    label = row.get("Label", "?")

    # If CICIDS2017 says this row is not benign traffic, print an alert.
    if label != "BENIGN":
        print(f"[ALERT] {label} flow inserted (processed so far: {count})")

    # For benign traffic, print progress after every 500 rows.
    elif count % 500 == 0:
        print(f"processed {count} rows so far...")
