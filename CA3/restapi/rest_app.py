# This program provides a small REST API for checking the database and viewing detected alerts.
#
# It is written to run in a Kubernetes pod, which adds these things to the basic job:
#   1. The MongoDB credentials can come from files that Kubernetes mounts from a Secret
#      (MONGO_USER_FILE and MONGO_PASSWORD_FILE).
#   2. /ready checks that MongoDB answers (used as the Kubernetes readiness probe), and /stats reports how many
#      flows are stored (used by the scaling test to measure how fast data arrives).
#   3. In the container it is served by gunicorn, a production web server, instead of Flask's development server.
#   4. It speaks HTTPS only (gunicorn is given the certificate and key, see the Dockerfile) and asks for a login (HTTP basic
#      authentication) on every address except /health and /ready, which carry no data and are what Kubernetes probes.
#   5. It reaches MongoDB over TLS as a user that may only READ the database ca3. Even a flaw in this program cannot write or delete data.
#   6. Every request leaves one JSON log line (time, level, service, method, path, status, duration in milliseconds),
#      plus the trace_id when the request asks for one event. A refused login (401) is logged as a warning.
#   7. The numbers (/metrics) are served on a second port, REST_METRICS_PORT, that needs no login and that only Prometheus may reach
#      (a NetworkPolicy says so): how many requests came in, with which answer, and how long they took.
#   8. When MongoDB does not answer, a request fails after 3 seconds with the answer 503 "database unavailable" instead of hanging,
#      and the failure shows up in the logs and in the error-rate number.

import hmac
import json
import os
import sys
import time
from datetime import datetime, timezone

from prometheus_client import Counter, Histogram, start_http_server

SERVICE = "restapi"


# One log line = one JSON object on standard output. Extra fields are given as keyword arguments.
def log(level, message, **fields):
    record = {"ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"), "level": level, "service": SERVICE, "msg": message}
    record.update(fields)
    print(json.dumps(record, separators=(",", ":"), default=str), flush=True)


# Stop the program with a message. Exit code 3 is the code that gunicorn understands as "the worker could not start": gunicorn then
# stops too, the container ends with the message in its log, and Kubernetes shows the failure. With any other exit code gunicorn would
# start a new worker every second, forever, and the pod would look alive but never become ready.
def die(message):
    print(message, file=sys.stderr, flush=True)
    sys.exit(3)


# Read one required setting from an environment variable.
#
# There is deliberately no default value. If a setting is missing, the program stops
# right away and names it, instead of quietly falling back to an old address or name.
def require(name):
    value = os.environ.get(name)

    if not value:
        die(f"missing required setting: {name}")

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
            die(f"cannot read {name}_FILE ({path}): {error}")

        if not value:
            die(f"{name}_FILE ({path}) is empty")

        return value

    value = os.environ.get(name)

    if not value:
        die(f"missing required setting: {name} (or {name}_FILE)")

    return value


# Read every setting before connecting to anything, so a missing one is reported first.
# Kubernetes provides them: the ConfigMap pipeline-config and the Secret mongo-credentials.

# MongoDB address, plus the database and collection that hold the network flows.
MONGO_HOST = require("MONGO_HOST")
MONGO_PORT = int(require("MONGO_PORT"))
DB_NAME = require("MONGO_DB")
COLLECTION_NAME = require("MONGO_COLLECTION")

# MongoDB username and password, read from the mounted Secret mongo-restapi (a user with read rights on ca3 only),
# and the certificate authority that signed MongoDB's certificate.
MONGO_USER = require_secret("MONGO_USER")
MONGO_PASSWORD = require_secret("MONGO_PASSWORD")
MONGO_TLS_CA = require("MONGO_TLS_CA_FILE")

# The login that callers of this API must give, read from the mounted Secret restapi-login.
REST_USER = require_secret("REST_USER")
REST_PASSWORD = require_secret("REST_PASSWORD")

# The port this REST API listens on, and the port where Prometheus reads its numbers.
REST_PORT = int(require("REST_PORT"))
METRICS_PORT = int(require("REST_METRICS_PORT"))


# These libraries are imported after the settings check on purpose,
# so a missing setting is always the first thing reported.
from flask import Flask, g, jsonify, request
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure
from werkzeug.exceptions import HTTPException


# Create the Flask application.
app = Flask(__name__)


# ---------------------------------------------------------------- the numbers (served at /metrics on METRICS_PORT)

REQUESTS = Counter("restapi_requests_total", "Requests answered by the REST API.", ["method", "endpoint", "status"])
DURATION = Histogram("restapi_request_duration_seconds", "Seconds from receiving a request to sending the answer.", ["endpoint"],
                     buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10))

# Addresses that need no login: the two Kubernetes probes. They return no data from the database.
OPEN_PATHS = ("/health", "/ready")

# Create the usual series at 0 right away. Prometheus cannot see the first increase of a series that appears out of nowhere,
# so without this the first refused login or the first failed request would be invisible on the dashboard.
for _endpoint, _statuses in (("/health", ("200",)), ("/ready", ("200", "503")), ("/alerts", ("200", "401", "503")),
                             ("/stats", ("200", "401", "503")), ("/events/<trace_id>", ("200", "401", "404", "503"))):
    DURATION.labels(_endpoint)

    for _status in _statuses:
        REQUESTS.labels("GET", _endpoint, _status)

# Start the metrics server. In the container there is one gunicorn worker process (see the Dockerfile), so there is one set of numbers.
start_http_server(METRICS_PORT)
log("info", "restapi starting", port=REST_PORT, metrics_port=METRICS_PORT, database=DB_NAME)


# Connect to MongoDB over TLS using the username and password. pymongo checks the server's certificate against the CA file,
# and checks that the certificate names the host ("mongo"). When MongoDB does not answer, a request waits at most 3 seconds
# (instead of pymongo's default of 30) and then fails with a clear error (see handle_error below).
client = MongoClient(
    MONGO_HOST,
    MONGO_PORT,
    username=MONGO_USER,
    password=MONGO_PASSWORD,
    tls=True,
    tlsCAFile=MONGO_TLS_CA,
    serverSelectionTimeoutMS=3000,
    connectTimeoutMS=3000,
)


# Use the configured database and collection.
collection = client[DB_NAME][COLLECTION_NAME]


# Runs first before every request: start a stopwatch. (Flask runs these functions in the order they are written, so this one
# comes before the login check, and a refused login is timed and counted too.)
@app.before_request
def start_stopwatch():
    g.started = time.perf_counter()


# Runs before every request. Everything except the probes needs the login: HTTP basic authentication, which is safe here because
# the connection is TLS. hmac.compare_digest compares in constant time, so the time a refusal takes tells nothing about the password.
@app.before_request
def require_login():
    if request.path in OPEN_PATHS:
        return None

    auth = request.authorization

    if auth is not None and auth.type == "basic":
        user_ok = hmac.compare_digest((auth.username or "").encode(), REST_USER.encode())
        password_ok = hmac.compare_digest((auth.password or "").encode(), REST_PASSWORD.encode())

        if user_ok and password_ok:
            return None

    return jsonify({"error": "login required"}), 401, {"WWW-Authenticate": 'Basic realm="ca3 pipeline"'}


# Runs after every request, whatever the answer was: count it, time it, and write one log line.
# The probes (/health and /ready) are logged only when they fail, otherwise they would fill the log every few seconds.
@app.after_request
def record_request(response):
    started = g.get("started")
    seconds = time.perf_counter() - started if started is not None else 0.0

    # The label is the address pattern ("/events/<trace_id>"), not the real address, so there are a few labels and not one per event.
    endpoint = request.url_rule.rule if request.url_rule is not None else "unmatched"
    status = response.status_code

    REQUESTS.labels(request.method, endpoint, str(status)).inc()
    DURATION.labels(endpoint).observe(seconds)

    if endpoint in OPEN_PATHS and status < 400:
        return response

    fields = {"method": request.method, "path": request.path, "status": status, "duration_ms": round(seconds * 1000, 1)}
    trace_id = (request.view_args or {}).get("trace_id")

    if trace_id:
        fields["trace_id"] = trace_id

    log("info" if status < 400 else ("warning" if status < 500 else "error"), "request", **fields)
    return response


# Turn every error into an answer, so a caller never gets a crash page and the log always says what happened.
#   - A wrong address or method (404, 405, ...): the normal answer for that error.
#   - MongoDB cannot be reached (it is down, restarting, or too slow): 503 "database unavailable". The caller may try again later.
#   - Anything else: 500, and the log line names the error.
@app.errorhandler(Exception)
def handle_error(error):
    if isinstance(error, HTTPException):
        return error

    if isinstance(error, ConnectionFailure):
        log("error", "database unavailable", error=error.__class__.__name__, path=request.path)
        return jsonify({"error": "database unavailable"}), 503

    log("error", "unexpected error", error=error.__class__.__name__, detail=str(error)[:200], path=request.path)
    return jsonify({"error": "internal error"}), 500


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
# For a local run give REST_TLS_CERT_FILE and REST_TLS_KEY_FILE too, so that it speaks HTTPS like the container does.
if __name__ == "__main__":
    cert = os.environ.get("REST_TLS_CERT_FILE")
    key = os.environ.get("REST_TLS_KEY_FILE")
    app.run(host="0.0.0.0", port=REST_PORT, ssl_context=(cert, key) if cert and key else None)
