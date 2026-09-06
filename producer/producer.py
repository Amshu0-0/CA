#!/usr/bin/env python3
import csv
import json
import time
from kafka import KafkaProducer

BROKER = "172.31.21.128:9092"
TOPIC = "network-flows"
CSV_FILE = "Friday-WorkingHours-Morning_pcap_ISCX.csv"
DELAY_SECONDS = 0.05   # small pause so the demo video actually shows something happening
LIMIT = None           # set to a number (e.g. 500) to only send the first N rows, or leave as None for the whole file

producer = KafkaProducer(
    bootstrap_servers=BROKER,
    value_serializer=lambda v: json.dumps(v).encode("utf-8"),
)

def main():
    with open(CSV_FILE, newline="") as f:
        reader = csv.DictReader(f)
        # CICIDS2017 headers have stray leading/trailing spaces (e.g. " Label") - clean them up
        reader.fieldnames = [name.strip() for name in reader.fieldnames]

        sent = 0
        for row in reader:
            producer.send(TOPIC, value=row)
            sent += 1
            if sent % 500 == 0:
                print(f"sent {sent} rows...")
            if DELAY_SECONDS:
                time.sleep(DELAY_SECONDS)
            if LIMIT and sent >= LIMIT:
                break

    producer.flush()
    print(f"done - sent {sent} rows total")

if __name__ == "__main__":
    main()
