# This program provides a small REST API for checking the database and for reading the results of the log-anomaly detectors.
# It is the CA3 REST API with new endpoints for the new data: replays ("runs"), windows with the verdict of each detector, single log
# lines, and a summary that compares every detector with the ground truth.
#
# It is written to run in a Kubernetes pod, which adds these things to the basic job (all of them are the same as in CA3):
#   1. The MongoDB credentials come from files that Kubernetes mounts from a Secret (MONGO_USER_FILE and MONGO_PASSWORD_FILE).
#   2. /ready checks that MongoDB answers (the Kubernetes readiness probe), and /stats reports how many documents are stored.
#   3. In the container it is served by gunicorn, a production web server, instead of Flask's development server.
#   4. It speaks HTTPS only and asks for a login (HTTP basic authentication) on every address except /health and /ready.
#   5. It reaches MongoDB over TLS as a user that may only READ its database. Even a flaw in this program cannot write or delete data.
#   6. Every request leaves one JSON log line (time, level, service, method, path, status, duration in milliseconds).
#   7. The numbers (/metrics) are served on a second port, REST_METRICS_PORT, that needs no login and that only Prometheus may reach.
#   8. When MongoDB does not answer, a request fails after 3 seconds with the answer 503 "database unavailable" instead of hanging.
#
# The addresses:
#   /health, /ready                       probes (no login)
#   /stats                                how many documents each collection holds
#   /runs                                 the replays, newest first, with their status and how many lines and windows they have
#   /summary?run=                         per detector: true/false positives and negatives, precision, recall, F1, on the windows of a run
#   /windows?run=&flagged=&limit=&skip=   the windows of a run in time order; flagged=truth|logbert|llm|keyword keeps those with that verdict 1
#   /events?run=&alert=1&limit=&skip=     the log lines of a run in order; alert=1 keeps the lines that carry an alert tag
#   /alerts                               up to 50 log lines that carry an alert tag (the lines of the latest run)
#   /events/<trace_id>                    the stored log line of the end-to-end smoke test
# "run" is optional everywhere: without it the newest run is used.

import hmac
import json
import os
import sys
import time
from datetime import datetime, timezone

from prometheus_client import Counter, Histogram, start_http_server

from summary import DETECTORS, summarize

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


# Read one required setting from an environment variable. No default: a missing setting stops the program and names it.
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
# Kubernetes provides them: the ConfigMap pipeline-config and the Secrets mongo-restapi and restapi-login.
MONGO_HOST = require("MONGO_HOST")
MONGO_PORT = int(require("MONGO_PORT"))
DB_NAME = require("MONGO_DB")
EVENTS_NAME = require("MONGO_COLLECTION")
WINDOWS_NAME = require("MONGO_WINDOWS_COLLECTION")
RUNS_NAME = require("MONGO_RUNS_COLLECTION")

MONGO_USER = require_secret("MONGO_USER")
MONGO_PASSWORD = require_secret("MONGO_PASSWORD")
MONGO_TLS_CA = require("MONGO_TLS_CA_FILE")

# The login that callers of this API must give, read from the mounted Secret restapi-login.
REST_USER = require_secret("REST_USER")
REST_PASSWORD = require_secret("REST_PASSWORD")

# The port this REST API listens on, and the port where Prometheus reads its numbers.
REST_PORT = int(require("REST_PORT"))
METRICS_PORT = int(require("REST_METRICS_PORT"))


# These libraries are imported after the settings check on purpose, so a missing setting is always the first thing reported.
from flask import Flask, g, jsonify, request
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure
from werkzeug.exceptions import HTTPException


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
                             ("/stats", ("200", "401", "503")), ("/runs", ("200", "401", "503")),
                             ("/summary", ("200", "400", "401", "404", "503")), ("/windows", ("200", "400", "401", "404", "503")),
                             ("/events", ("200", "400", "401", "404", "503")),
                             ("/events/<trace_id>", ("200", "401", "404", "503"))):
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

database = client[DB_NAME]
events = database[EVENTS_NAME]
windows = database[WINDOWS_NAME]
runs = database[RUNS_NAME]

# The most that one request may return, and the default.
MAX_LIMIT = 500
DEFAULT_LIMIT = 50

# What a caller may ask for in ?flagged= (the field of the window documents).
FLAGGED_FIELDS = ("truth",) + tuple(field for field, _ in DETECTORS)


# Runs first before every request: start a stopwatch, so even a refused login is timed and counted.
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

    return jsonify({"error": "login required"}), 401, {"WWW-Authenticate": 'Basic realm="nlp pipeline"'}


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
@app.errorhandler(Exception)
def handle_error(error):
    if isinstance(error, HTTPException):
        return error

    if isinstance(error, ConnectionFailure):
        log("error", "database unavailable", error=error.__class__.__name__, path=request.path)
        return jsonify({"error": "database unavailable"}), 503

    log("error", "unexpected error", error=error.__class__.__name__, detail=str(error)[:200], path=request.path)
    return jsonify({"error": "internal error"}), 500


# A request that asks for something that cannot be: the answer says what is wrong (400).
class BadRequest(Exception):
    pass


@app.errorhandler(BadRequest)
def handle_bad_request(error):
    return jsonify({"error": str(error)}), 400


# A whole number from the query string, between low and high.
def number(name, default, low, high):
    text = request.args.get(name)

    if text is None:
        return default

    try:
        value = int(text)
    except ValueError:
        raise BadRequest(f"{name} must be a whole number") from None

    if not low <= value <= high:
        raise BadRequest(f"{name} must be between {low} and {high}")

    return value


# The run that a request is about: ?run=, or else the newest run (the one that started last). None if there is no run yet.
def pick_run():
    wanted = request.args.get("run")

    if wanted:
        return wanted

    newest = runs.find_one({}, {"_id": 1}, sort=[("started_at", -1)])
    return None if newest is None else newest["_id"]


# The answer for "there is no such run" (or no run at all).
def no_run(run):
    return jsonify({"found": False, "run": run, "error": "no such run" if run else "no replay has been stored yet"}), 404


# A run document without the bookkeeping that the processor needs for a restart.
def public_run(document):
    hidden = ("origin", "next_k", "last_key", "done")
    shown = {key: value for key, value in document.items() if key not in hidden and key != "_id"}
    return {"run": document["_id"], **shown}


# Simple health endpoint used to confirm that the REST API itself is running (the liveness probe).
@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


# Readiness endpoint: only answers 200 when MongoDB answers too (the readiness probe).
@app.route("/ready", methods=["GET"])
def ready():
    try:
        client.admin.command("ping")
    except Exception as error:
        return jsonify({"status": "mongodb unreachable", "error": error.__class__.__name__}), 503

    return jsonify({"status": "ready"})


# How many documents are stored. estimated_document_count() reads collection metadata, so it stays fast with millions of documents.
# "documents" is the number of log lines (what the CA3 version called flows).
@app.route("/stats", methods=["GET"])
def get_stats():
    counts = {name: collection.estimated_document_count() for name, collection in ((EVENTS_NAME, events), (WINDOWS_NAME, windows), (RUNS_NAME, runs))}

    return jsonify({"database": DB_NAME, "collection": EVENTS_NAME, "documents": counts[EVENTS_NAME], "collections": counts})


# The replays, newest first, each with the number of lines and windows stored for it.
@app.route("/runs", methods=["GET"])
def get_runs():
    limit = number("limit", 20, 1, 100)
    found = []

    for document in runs.find({}).sort("started_at", -1).limit(limit):
        item = public_run(document)
        item["lines_stored"] = events.count_documents({"run": document["_id"]})
        item["windows_stored"] = windows.count_documents({"run": document["_id"]})
        found.append(item)

    return jsonify({"count": len(found), "runs": found})


# How well each detector did on the windows of a run: the numbers of the NLP project's evaluation, for this replay.
@app.route("/summary", methods=["GET"])
def get_summary():
    run = pick_run()
    document = None if run is None else runs.find_one({"_id": run})

    if document is None:
        return no_run(run)

    fields = {"truth": 1, "n_messages": 1, "score_ms": 1, "_id": 0, **{field: 1 for field, _ in DETECTORS}}
    result = summarize(windows.find({"run": run}, fields))

    lines = events.count_documents({"run": run})
    alert_lines = events.count_documents({"run": run, "alert": 1})
    unseen = events.count_documents({"run": run, "template_id": -1})
    no_decision = events.count_documents({"run": run, "llm": None})

    return jsonify({
        "run": public_run(document),
        "lines": {"stored": lines, "with_alert_tag": alert_lines, "template_not_known": unseen, "llm_has_no_saved_decision": no_decision},
        **result,
    })


# The windows of a run in time order. ?flagged=truth|logbert|llm|keyword keeps the windows whose verdict (or the truth) is 1.
@app.route("/windows", methods=["GET"])
def get_windows():
    run = pick_run()

    if run is None or runs.find_one({"_id": run}, {"_id": 1}) is None:
        return no_run(run)

    query = {"run": run}
    flagged = request.args.get("flagged")

    if flagged:
        if flagged not in FLAGGED_FIELDS:
            raise BadRequest("flagged must be one of " + ", ".join(FLAGGED_FIELDS))

        query[flagged] = 1

    limit, skip = number("limit", DEFAULT_LIMIT, 1, MAX_LIMIT), number("skip", 0, 0, 10_000_000)
    found = list(windows.find(query, {"_id": 0}).sort("start", 1).skip(skip).limit(limit))

    return jsonify({"run": run, "count": len(found), "skip": skip, "windows": found})


# The log lines of a run in order. ?alert=1 keeps the lines that carry an alert tag; ?template_id= keeps one template.
@app.route("/events", methods=["GET"])
def get_events():
    run = pick_run()

    if run is None or runs.find_one({"_id": run}, {"_id": 1}) is None:
        return no_run(run)

    query = {"run": run}

    if request.args.get("alert") is not None:
        query["alert"] = number("alert", 1, 0, 1)

    if request.args.get("template_id") is not None:
        query["template_id"] = number("template_id", 0, -1, 10_000_000)

    limit, skip = number("limit", DEFAULT_LIMIT, 1, MAX_LIMIT), number("skip", 0, 0, 10_000_000)
    found = list(events.find(query, {"_id": 0}).sort("offset", 1).skip(skip).limit(limit))

    return jsonify({"run": run, "count": len(found), "skip": skip, "events": found})


# Up to 50 log lines of the newest run that carry an alert tag: the lines that the dataset marks as anomalies.
@app.route("/alerts", methods=["GET"])
def get_alerts():
    run = pick_run()
    query = {"alert": 1, "smoke": {"$ne": True}}

    if run is not None:
        query["run"] = run

    found = list(events.find(query, {"_id": 0}).sort("offset", 1).limit(50))
    return jsonify({"count": len(found), "alerts": found})


# Look up one stored line by the trace_id it carries. The end-to-end smoke test sends one line with a unique trace_id, then asks this
# address for it, to prove that the line went all the way through the pipeline. The answer is JSON in both cases, so a caller can tell
# "this line is not here" (found: false) apart from "this URL does not exist" (an HTML error page).
@app.route("/events/<trace_id>", methods=["GET"])
def get_event(trace_id):
    event = events.find_one({"trace_id": trace_id}, {"_id": 0})

    if event is None:
        return jsonify({"found": False, "trace_id": trace_id}), 404

    return jsonify({"found": True, "event": event})


# Start Flask's built-in server. Only used for a quick local run: the container starts gunicorn instead (see the Dockerfile).
if __name__ == "__main__":
    cert = os.environ.get("REST_TLS_CERT_FILE")
    key = os.environ.get("REST_TLS_KEY_FILE")
    app.run(host="0.0.0.0", port=REST_PORT, ssl_context=(cert, key) if cert and key else None)
