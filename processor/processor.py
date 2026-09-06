#!/usr/bin/env python3
import json
from kafka import KafkaConsumer
from pymongo import MongoClient

BROKER = "172.31.21.128:9092"
TOPIC = "network-flows"
MONGO_HOST = "172.31.24.111"
MONGO_PORT = 27017
DB_NAME = "ca0"
COLLECTION_NAME = "flows"

consumer = KafkaConsumer(
    TOPIC,
    bootstrap_servers=BROKER,
    value_deserializer=lambda v: json.loads(v.decode("utf-8")),
    auto_offset_reset="earliest",
    group_id="processor-group",
)

client = MongoClient(MONGO_HOST, MONGO_PORT)
collection = client[DB_NAME][COLLECTION_NAME]

print("processor listening on topic:", TOPIC)
count = 0
for message in consumer:
    row = message.value
    collection.insert_one(row)
    count += 1

    label = row.get("Label", "?")
    if label != "BENIGN":
        print(f"[ALERT] {label} flow inserted (processed so far: {count})")
    elif count % 500 == 0:
        print(f"processed {count} rows so far...")
