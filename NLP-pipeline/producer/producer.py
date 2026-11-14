# This program replays a file of BGL system-log lines into Kafka, or sends one tagged test line. It is the CA3 producer, which replayed
# network flows, with the data replaced: the pipeline around it is the same.
#
# What it sends (one JSON object per Kafka message):
#   {"kind": "log",   "run": "run-20261114-091500", "seq": 17, "line": "<one raw BGL line>"}    one line of the replay
#   {"kind": "end",   "run": "run-20261114-091500", "lines": 30000}                             after the last line: the replay is complete
#   {"kind": "smoke", "trace_id": "smoke-1", "line": "<one raw BGL line>"}                       the end-to-end test (--trace-id)
#
# Things that matter for the windows of the NLP step (see processor/engine.py):
#   - The file is replayed in file order, one message at a time (max_in_flight_requests_per_connection=1, acks=all), so Kafka keeps the
#     order. The export script sorted the file by time, exactly as the NLP project sorts the log before it builds its windows.
#   - If Kafka does not accept a line, the producer stops with an error BEFORE it sends the "end" marker. A replay with a hole in it must
#     not look like a finished one. (The CA3 producer counted the failure and went on; here a missing line would change the windows.)
#   - The raw line still carries the alert tag (the ground truth of the dataset). The processor splits it off in its first step and keeps it
#     for the evaluation only; no detector ever sees it. It travels with the line because this is an evaluation replay.
#
# What is kept from CA3: the settings come from the ConfigMap pipeline-config (no defaults for where to connect), Kafka is reached over TLS
# with a client certificate (mutual TLS), SIGTERM stops the program, every log line is one JSON object, and /metrics serves the numbers.

import argparse
import json
import os
import signal
import sys
import time
from datetime import datetime, timezone

from prometheus_client import Counter, Gauge, Histogram, start_http_server

SERVICE = "producer"

# After this many lines the producer waits until Kafka has confirmed them. Without it the library's queue in memory grows without limit.
DEFAULT_FLUSH_EVERY = 500


# One log line = one JSON object on standard output. Extra fields are given as keyword arguments.
def log(level, message, **fields):
    record = {"ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"), "level": level, "service": SERVICE, "msg": message}
    record.update(fields)
    print(json.dumps(record, separators=(",", ":"), default=str), flush=True)


# One required setting from an environment variable. No default: a missing setting stops the program and names it.
def require(name):
    value = os.environ.get(name)

    if not value:
        sys.exit(f"missing required setting: {name}")

    return value


# Every setting, read before anything is connected, so a missing one is reported first.
class Settings:
    def __init__(self):
        self.broker = require("KAFKA_BROKER")
        self.topic = require("KAFKA_TOPIC")

        # The file to replay. It is baked into the image (NLP-pipeline/bundle/bgl-replay.log).
        self.dataset = require("DATASET_FILE")

        self.ssl_ca = require("KAFKA_SSL_CA_FILE")
        self.ssl_cert = require("KAFKA_SSL_CERT_FILE")
        self.ssl_key = require("KAFKA_SSL_KEY_FILE")
        self.metrics_port = int(require("PRODUCER_METRICS_PORT"))

        # Optional tuning: how fast and how much, never where. 0 lines per second means as fast as Kafka accepts them.
        self.lines_per_second = float(os.environ.get("LINES_PER_SECOND", "0"))
        self.limit = int(os.environ["MAX_ROWS"]) if os.environ.get("MAX_ROWS") else None
        self.flush_every = int(os.environ.get("FLUSH_EVERY_ROWS", str(DEFAULT_FLUSH_EVERY)))

        # The name of this replay. Every document that the processor stores carries it. One is made up if none is given.
        self.run = os.environ.get("RUN_ID") or datetime.now(timezone.utc).strftime("run-%Y%m%d-%H%M%S")


# ---------------------------------------------------------------- the numbers (served at /metrics)

SENT = Counter("producer_messages_sent_total", "Messages handed to Kafka by this producer (log lines, and the end marker).")
FAILED = Counter("producer_send_errors_total", "Messages that Kafka did not accept (Kafka unreachable or too slow).")
FLUSH_SECONDS = Histogram("producer_flush_seconds", "Seconds spent waiting for Kafka to confirm a batch of lines. It grows when Kafka is the slow part.",
                          buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30))
SENDING = Gauge("producer_sending", "1 while this pod replays the file, 0 when it is finished.")
SENDING.set(0)


class ReplayFailed(Exception):
    pass


# ---------------------------------------------------------------- the replay

# The lines of the replay file, in file order, without empty lines. (Lines that are not BGL lines are not removed here: the processor
# counts and skips them, which makes them visible.)
def read_lines(path, limit=None):
    lines = []

    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.rstrip("\r\n")

            if line.strip():
                lines.append(line)

            if limit and len(lines) >= limit:
                break

    return lines


# Sleep just long enough to keep to `rate` lines per second since `started`.
def pace(started, sent, rate):
    if rate > 0:
        wait = started + sent / rate - time.time()

        if wait > 0.005:
            time.sleep(wait)


# Send the lines, then the end marker. `producer` is a KafkaProducer (or a stand-in in the tests); `stop()` says whether to leave early.
# Returns the number of lines that Kafka accepted. Raises ReplayFailed if it did not accept all of them (no end marker is sent then).
def replay(producer, topic, run, lines, rate=0.0, flush_every=DEFAULT_FLUSH_EVERY, stop=lambda: False):
    failures = []

    def failed(error):
        failures.append(f"{error.__class__.__name__}: {str(error)[:200]}")
        FAILED.inc()

    def flush():
        started = time.time()
        producer.flush(timeout=60)
        FLUSH_SECONDS.observe(time.time() - started)

        if failures:
            raise ReplayFailed(f"Kafka did not accept {len(failures)} message(s); the first error was {failures[0]}. "
                               "No end marker is sent: a replay with a hole in it must not look complete.")

    started = time.time()
    sent = 0
    SENDING.set(1)

    try:
        for seq, line in enumerate(lines):
            if stop():
                log("warning", "interrupted: the end marker is not sent", run=run, lines_sent=sent)
                flush()
                return sent

            producer.send(topic, value={"kind": "log", "run": run, "seq": seq, "line": line}).add_errback(failed)
            sent += 1
            SENT.inc()

            if sent % flush_every == 0:
                flush()
                log("info", "progress", run=run, lines_sent=sent, topic=topic)

            pace(started, sent, rate)

        flush()
        producer.send(topic, value={"kind": "end", "run": run, "lines": sent}).add_errback(failed)
        SENT.inc()
        flush()
    finally:
        SENDING.set(0)

    return sent


# The first line with an alert tag (the first field is not "-"), or the first line if there is none.
def first_alert_line(lines):
    for line in lines:
        fields = line.split(None, 1)

        if fields and fields[0] != "-":
            return line

    return lines[0]


# Send ONE line that carries a unique trace_id, then report exactly where Kafka stored it. This is the end-to-end smoke test: the processor
# logs the same trace_id with the same partition and offset, and the REST API returns the stored document. The line is the first alert
# line of the file, so the verdicts that are stored with it are worth looking at.
def send_one_line(producer, topic, lines, trace_id):
    line = first_alert_line(lines)

    # Waiting for the answer means Kafka has really stored the message. It says which topic, partition and offset.
    stored = producer.send(topic, value={"kind": "smoke", "trace_id": trace_id, "line": line}).get(timeout=15)
    SENT.inc()
    log("info", "sent", trace_id=trace_id, topic=stored.topic, partition=stored.partition, offset=stored.offset)
    return stored


def build_producer(settings):
    from kafka import KafkaProducer

    # Kafka over TLS. ssl_check_hostname makes the producer check that the certificate Kafka shows names the host it connected to.
    return KafkaProducer(
        bootstrap_servers=settings.broker,
        security_protocol="SSL",
        ssl_cafile=settings.ssl_ca,
        ssl_certfile=settings.ssl_cert,
        ssl_keyfile=settings.ssl_key,
        ssl_check_hostname=True,
        value_serializer=lambda v: json.dumps(v).encode("utf-8"),

        # The lines must reach Kafka in file order, also when a send is repeated: one request at a time, confirmed by the broker.
        acks="all",
        retries=5,
        max_in_flight_requests_per_connection=1,
        linger_ms=5,
    )


def main():
    parser = argparse.ArgumentParser(description="Replay the BGL file into Kafka, or send one tagged test line.")
    parser.add_argument("--trace-id", help="send a single test line carrying this trace id, instead of replaying the file")
    args = parser.parse_args()

    settings = Settings()
    running = True

    def stop_signal(signum, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, stop_signal)
    signal.signal(signal.SIGINT, stop_signal)

    lines = read_lines(settings.dataset, settings.limit)

    if not lines:
        sys.exit(f"{settings.dataset} has no lines to replay")

    producer = build_producer(settings)

    if args.trace_id:
        send_one_line(producer, settings.topic, lines, args.trace_id)
        producer.close()
        return

    # Prometheus reads the numbers from here, after the connection to Kafka worked, so "target is up" means "really connected".
    start_http_server(settings.metrics_port)
    log("info", "producer started", topic=settings.topic, run=settings.run, lines_in_file=len(lines), lines_per_second=settings.lines_per_second)

    try:
        sent = replay(producer, settings.topic, settings.run, lines, settings.lines_per_second, settings.flush_every, lambda: not running)
    except ReplayFailed as error:
        log("error", "replay failed", run=settings.run, detail=str(error))
        sys.exit(1)

    producer.close()
    log("info", "producer done", run=settings.run, lines_sent=sent, topic=settings.topic)


if __name__ == "__main__":
    main()
