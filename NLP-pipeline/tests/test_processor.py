# processor.py without a cluster: a fake Kafka consumer and the in-memory sink stand in for Kafka and MongoDB.

import json
import os
import urllib.error
import urllib.request
from collections import namedtuple

import pytest
from prometheus_client import REGISTRY

import processor
from engine import Engine
from fakes import FakeDetectors, make_records
from sink import MemorySink

Message = namedtuple("Message", "topic partition offset timestamp value")


def counter(name, **labels):
    return REGISTRY.get_sample_value(name, labels) or 0.0


class FakeConsumer:
    def __init__(self, records, size=40, partition=lambda record: record.partition):
        self.pending = list(records)
        self.size = size
        self.partition = partition
        self.commits = []

    def poll(self, timeout_ms=0, max_records=500):
        batch, self.pending = self.pending[: min(self.size, max_records)], self.pending[min(self.size, max_records):]
        messages = [Message(r.topic, self.partition(r), r.offset, r.timestamp_ms, r.value) for r in batch]
        return {("tp", 0): messages} if messages else {}

    def commit(self, offsets):
        self.commits.append(offsets)


def run_serve(records, sink=None, size=40, partition=lambda record: record.partition):
    consumer = FakeConsumer(records, size, partition)
    sink = sink or MemorySink()
    engine = Engine(FakeDetectors(), sink.load_state, lambda: 7.0)
    count = processor.serve(consumer, engine, sink, "system-logs", lambda: not consumer.pending)
    return consumer, sink, engine, count


def test_the_loop_writes_commits_and_counts():
    records = make_records(1, n=300)
    before = {name: counter(name) for name in ("processor_messages_total", "processor_windows_total", "processor_alerts_total", "processor_insert_batches_total")}
    consumer, sink, engine, count = run_serve(records)

    assert count == 300 and len(sink.events) == 300 and len(sink.windows) > 10
    assert sink.runs["r1"]["status"] == "complete"

    # one commit per batch, in the form kafka-python wants, ending just after the last message
    assert len(consumer.commits) == 8
    last = consumer.commits[-1]
    ((partition, offset),) = last.items()
    assert partition.topic == "system-logs" and partition.partition == 0 and offset.offset == records[-1].offset + 1

    assert counter("processor_messages_total") - before["processor_messages_total"] == 300
    assert counter("processor_windows_total") - before["processor_windows_total"] == len(sink.windows)
    assert counter("processor_alerts_total") - before["processor_alerts_total"] == sum(e["alert"] for e in sink.events.values())
    assert counter("processor_insert_batches_total") - before["processor_insert_batches_total"] == 8
    assert counter("processor_windows_flagged_total", detector="logbert") > 0


def test_no_commit_goes_past_a_line_that_an_open_window_still_needs():
    records = make_records(2, n=200, end=False)
    consumer, sink, engine, _ = run_serve(records)

    for committed, batch_end in zip(consumer.commits, range(40, 201, 40)):
        ((_, offset),) = committed.items()
        assert offset.offset <= batch_end


def test_when_mongodb_fails_the_batch_is_not_committed():
    class Failing(MemorySink):
        def write_windows(self, documents):
            if documents:
                raise ConnectionError("MongoDB is gone")

            super().write_windows(documents)

    records = make_records(3, n=200)
    consumer = FakeConsumer(records)
    sink = Failing()
    engine = Engine(FakeDetectors(), sink.load_state, lambda: 7.0)

    with pytest.raises(ConnectionError):
        processor.serve(consumer, engine, sink, "system-logs", lambda: False)

    # The batch that failed (the first one that completed a window) was not committed, and neither was anything after it: the batches
    # before it were 40 messages each, so no committed offset can lie beyond them.
    committed = [offset.offset for commit in consumer.commits for offset in commit.values()]
    assert len(consumer.commits) < 8
    assert all(offset <= 40 * len(consumer.commits) for offset in committed)
    assert sink.windows == {}


def test_a_second_partition_stops_the_processor():
    records = make_records(4, n=100)

    with pytest.raises(RuntimeError, match="more than one partition"):
        run_serve(records, partition=lambda record: record.offset % 2)


def test_a_message_that_is_not_json_is_read_as_none():
    assert processor.read_json(b"not json") is None
    assert processor.read_json("é".encode("latin-1")) is None
    assert processor.read_json(json.dumps({"kind": "end"}).encode()) == {"kind": "end"}


def test_a_missing_setting_is_named(monkeypatch):
    for name in list(os.environ):
        if name.startswith(("KAFKA_", "MONGO_")) or name in ("CONSUMER_GROUP", "METRICS_PORT", "BUNDLE_DIR"):
            monkeypatch.delenv(name)

    with pytest.raises(SystemExit, match="missing required setting: KAFKA_BROKER"):
        processor.Settings()


def test_all_settings_are_read_from_the_environment_and_the_files(monkeypatch, tmp_path):
    (tmp_path / "user").write_text("processor\n")
    (tmp_path / "password").write_text("secret-value\n")
    values = {"KAFKA_BROKER": "kafka:9092", "KAFKA_TOPIC": "system-logs", "CONSUMER_GROUP": "g", "KAFKA_SSL_CA_FILE": "/ca", "KAFKA_SSL_CERT_FILE": "/c",
              "KAFKA_SSL_KEY_FILE": "/k", "MONGO_TLS_CA_FILE": "/mca", "MONGO_HOST": "mongo", "MONGO_PORT": "27017", "MONGO_DB": "nlp",
              "MONGO_COLLECTION": "events", "MONGO_WINDOWS_COLLECTION": "windows", "MONGO_RUNS_COLLECTION": "runs", "METRICS_PORT": "8000",
              "BUNDLE_DIR": "/bundle", "MONGO_USER_FILE": str(tmp_path / "user"), "MONGO_PASSWORD_FILE": str(tmp_path / "password")}

    for name, value in values.items():
        monkeypatch.setenv(name, value)

    settings = processor.Settings()
    assert (settings.mongo_user, settings.mongo_password, settings.mongo_port, settings.torch_threads) == ("processor", "secret-value", 27017, 2)
    assert (settings.events, settings.windows, settings.runs, settings.bundle) == ("events", "windows", "runs", "/bundle")


def test_the_health_pages():
    server = processor.start_health_server(0)
    base = f"http://127.0.0.1:{server.server_address[1]}"

    def get(path):
        try:
            with urllib.request.urlopen(base + path, timeout=5) as response:
                return response.status, response.read().decode()
        except urllib.error.HTTPError as error:
            return error.code, error.read().decode()

    try:
        processor.set_ready(False)
        assert get("/readyz")[0] == 503 and get("/healthz")[0] == 200 and get("/nothing")[0] == 404

        processor.set_ready(True)
        assert get("/readyz")[0] == 200

        status, body = get("/metrics")
        assert status == 200 and "processor_messages_total" in body and "processor_window_score_seconds" in body

        # a loop that has not asked Kafka for too long is reported as stuck
        processor.state.looping, processor.state.last_poll_time = True, 0
        assert get("/healthz")[0] == 503
    finally:
        processor.state.looping = False
        processor.set_ready(False)
        server.shutdown()


def test_the_offset_is_made_the_way_the_installed_kafka_python_wants_it():
    offset = processor.offset_and_metadata(42)
    assert offset.offset == 42 and offset.metadata == ""
