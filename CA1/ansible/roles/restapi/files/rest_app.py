#!/usr/bin/env python3

# This program provides a small REST API for checking the database and viewing detected alerts.

import os

from flask import Flask, jsonify
from pymongo import MongoClient


# Create the Flask application.
app = Flask(__name__)


# Get the MongoDB username and password from environment variables.
# These values will come from the encrypted Ansible Vault.
MONGO_USER = os.environ.get("MONGO_USER", "")
MONGO_PASSWORD = os.environ.get("MONGO_PASSWORD", "")


# Connect to MongoDB running on this same VM.
# Use authentication when a username and password are provided.
#
# The else block keeps the old CA0 behavior available for local testing without MongoDB authentication.
if MONGO_USER and MONGO_PASSWORD:
    client = MongoClient(
        "localhost",
        27017,
        username=MONGO_USER,
        password=MONGO_PASSWORD,
    )
else:
    client = MongoClient("localhost", 27017)


# Use the ca0 database and the flows collection.
collection = client["ca0"]["flows"]


# Simple health endpoint used to confirm that the REST API itself is running.
@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


# Return up to 50 network flows that are not BENIGN.
# These are the flows treated as alerts by the pipeline.
@app.route("/alerts", methods=["GET"])
def get_alerts():
    alerts = list(
        collection.find(
            {"Label": {"$ne": "BENIGN"}},
            {"_id": 0},
        ).limit(50)
    )

    return jsonify({
        "count": len(alerts),
        "alerts": alerts,
    })


# Start the Flask API on port 8080.
# 0.0.0.0 allows requests to reach it from outside the container or VM.
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
