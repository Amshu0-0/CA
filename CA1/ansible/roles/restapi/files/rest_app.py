#!/usr/bin/env python3

# This program provides a small REST API for checking the database and viewing detected alerts.

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
# Ansible provides all of these in an environment file for the systemd service.
# The values come from config.yml and the encrypted Ansible Vault.

# MongoDB address, plus the database and collection that hold the network flows.
# MongoDB runs on this same VM, so Ansible sets the address to localhost.
MONGO_HOST = require("MONGO_HOST")
MONGO_PORT = int(require("MONGO_PORT"))
DB_NAME = require("MONGO_DB")
COLLECTION_NAME = require("MONGO_COLLECTION")

# MongoDB username and password. These come from the encrypted Ansible Vault.
MONGO_USER = require("MONGO_USER")
MONGO_PASSWORD = require("MONGO_PASSWORD")

# The port this REST API listens on.
REST_PORT = int(require("REST_PORT"))


# These libraries are imported after the settings check on purpose,
# so a missing setting is always the first thing reported.
from flask import Flask, jsonify
from pymongo import MongoClient


# Create the Flask application.
app = Flask(__name__)


# Connect to MongoDB using the username and password.
client = MongoClient(
    MONGO_HOST,
    MONGO_PORT,
    username=MONGO_USER,
    password=MONGO_PASSWORD,
)


# Use the configured database and collection.
collection = client[DB_NAME][COLLECTION_NAME]


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


# Look up one event by the trace_id it carries.
#
# Real traffic from the dataset has no trace_id. The end-to-end smoke test tags one event with a unique
# trace_id, then asks this endpoint for it to prove the event made it all the way through the pipeline.
# The answer is JSON in both cases, so a caller can tell "this event is not here" (found: false)
# apart from "this URL does not exist" (an HTML error page).
@app.route("/events/<trace_id>", methods=["GET"])
def get_event(trace_id):
    event = collection.find_one({"trace_id": trace_id}, {"_id": 0})

    if event is None:
        return jsonify({"found": False, "trace_id": trace_id}), 404

    return jsonify({"found": True, "event": event})


# Start the Flask API on the configured port.
# 0.0.0.0 allows requests to reach it from outside the container or VM.
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=REST_PORT)
