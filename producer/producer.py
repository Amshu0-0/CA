#!/usr/bin/env python3

import csv
import json
import time

from kafka import KafkaProducer


# Private IP and port of the Kafka
BROKER = os.environ.get("KAFKA_BROKER", "172.31.21.128:9092")

# Kafka topic where the network flow data will be sent
TOPIC = "network-flows"

CSV_FILE = "Friday-Morning-5000-mixed-bot.csv"

# This slows the replay down so we can see the data moving during the demo
DELAY_SECONDS = 0

#  Send every row in the csv, no limit
LIMIT = None


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
