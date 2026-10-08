# This program provides a small REST API for checking the database and viewing detected alerts.
#
# It is written to run in a Kubernetes pod, which adds three things to the basic job:
#   1. The MongoDB credentials can come from files that Kubernetes mounts from a Secret
#      (MONGO_USER_FILE and MONGO_PASSWORD_FILE).
#   2. /ready checks that MongoDB answers (used as the Kubernetes readiness probe), and /stats reports how many
#      flows are stored (used by the scaling test to measure how fast data arrives).
#   3. In the container it is served by gunicorn, a production web server, instead of Flask's development server.

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


# Read one required credential: from the file named by NAME_FILE (a mounted Kubernetes Secret),
# or, for a quick local run, from the NAME environment variable.
def require_secret(name):
    path = os.environ.get(name + "_FILE")

    if path:
        try:
            with open(path) as f:
                value = f.read().rstrip("\n")
        except OSError as error:
            sys.exit(f"cannot read {name}_FILE ({path}): {error}")

        if not value:
            sys.exit(f"{name}_FILE ({path}) is empty")

        return value

    value = os.environ.get(name)

    if not value:
        sys.exit(f"missing required setting: {name} (or {name}_FILE)")

    return value


# Read every setting before connecting to anything, so a missing one is reported first.
# Kubernetes provides them: the ConfigMap pipeline-config and the Secret mongo-credentials.

# MongoDB address, plus the database and collection that hold the network flows.
MONGO_HOST = require("MONGO_HOST")
MONGO_PORT = int(require("MONGO_PORT"))
DB_NAME = require("MONGO_DB")
COLLECTION_NAME = require("MONGO_COLLECTION")

# MongoDB username and password, read from the mounted Secret.
MONGO_USER = require_secret("MONGO_USER")
MONGO_PASSWORD = require_secret("MONGO_PASSWORD")

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


# Simple health endpoint used to confirm that the REST API itself is running (the liveness probe).
@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


# Readiness endpoint: only answers 200 when MongoDB answers too (the readiness probe).
# Until then Kubernetes keeps this pod out of the Service, so no request reaches a pod that cannot answer it.
@app.route("/ready", methods=["GET"])
def ready():
    try:
        client.admin.command("ping")
    except Exception as error:
        return jsonify({"status": "mongodb unreachable", "error": error.__class__.__name__}), 503

    return jsonify({"status": "ready"})


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


# Report how many flows are stored. The scaling test polls this to measure how fast data reaches the database.
# estimated_document_count() reads collection metadata, so it stays fast even when millions of flows are stored.
@app.route("/stats", methods=["GET"])
def get_stats():
    return jsonify({
        "database": DB_NAME,
        "collection": COLLECTION_NAME,
        "documents": collection.estimated_document_count(),
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


# Start Flask's built-in server. Only used for a quick local run: the container starts gunicorn instead (see the Dockerfile).
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=REST_PORT)
