# producer.py with a fake Kafka producer.

import json
from collections import namedtuple

import pytest
from prometheus_client import REGISTRY

import producer
from fakes import bgl_line

Stored = namedtuple("Stored", "topic partition offset")


class FakeFuture:
    def __init__(self, owner, error=None, stored=None):
        self.owner, self.error, self.stored = owner, error, stored

    def add_errback(self, function):
        if self.error is not None:
            self.owner.errbacks.append((function, self.error))

        return self

    def get(self, timeout=None):
        return self.stored


class FakeProducer:
    def __init__(self, fail_after=None):
        self.sent, self.errbacks, self.flushes, self.fail_after = [], [], 0, fail_after

    def send(self, topic, value):
        offset = len(self.sent)
        self.sent.append((topic, value))
        error = ConnectionError("broker gone") if self.fail_after is not None and offset >= self.fail_after else None
        return FakeFuture(self, error, Stored(topic, 0, offset))

    def flush(self, timeout=None):
        self.flushes += 1

        for function, error in self.errbacks:
            function(error)

        self.errbacks = []


def lines(n):
    return [bgl_line(1000 + i, i % 5, "KERNDTLB" if i == 3 else "-", f"x{i}") for i in range(n)]


def test_the_replay_sends_the_lines_in_order_and_then_the_end_marker():
    fake = FakeProducer()
    sent = producer.replay(fake, "system-logs", "run-1", lines(120), flush_every=50)

    assert sent == 120 and len(fake.sent) == 121
    assert all(topic == "system-logs" for topic, _ in fake.sent)
    values = [value for _, value in fake.sent]
    assert [v["seq"] for v in values[:-1]] == list(range(120)) and {v["run"] for v in values} == {"run-1"}
    assert [v["line"] for v in values[:-1]] == lines(120)
    assert values[-1] == {"kind": "end", "run": "run-1", "lines": 120}

    # the messages are valid JSON as the serializer makes them
    json.dumps(values[0])
    assert fake.flushes >= 3


def test_a_line_that_kafka_does_not_accept_stops_the_replay_without_an_end_marker():
    fake = FakeProducer(fail_after=60)

    with pytest.raises(producer.ReplayFailed, match="No end marker"):
        producer.replay(fake, "system-logs", "run-2", lines(120), flush_every=50)

    assert all(value["kind"] == "log" for _, value in fake.sent)


def test_a_stop_request_ends_the_replay_without_an_end_marker():
    fake = FakeProducer()
    calls = iter(range(1000))
    sent = producer.replay(fake, "system-logs", "run-3", lines(100), flush_every=50, stop=lambda: next(calls) >= 30)

    assert sent == 30 and all(value["kind"] == "log" for _, value in fake.sent)


def test_the_smoke_line_is_the_first_alert_line_and_the_answer_says_where_it_was_stored(capsys):
    fake = FakeProducer()
    stored = producer.send_one_line(fake, "system-logs", lines(10), "smoke-42")

    ((topic, value),) = fake.sent
    assert value == {"kind": "smoke", "trace_id": "smoke-42", "line": lines(10)[3]}
    assert (stored.topic, stored.partition, stored.offset) == ("system-logs", 0, 0)

    entry = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert entry["msg"] == "sent" and entry["trace_id"] == "smoke-42" and entry["partition"] == 0 and entry["offset"] == 0

    # no alert line in the file: the first line
    assert producer.first_alert_line([bgl_line(1, 1), bgl_line(2, 2)]) == bgl_line(1, 1)


def test_the_file_is_read_in_order_without_empty_lines(tmp_path):
    path = tmp_path / "replay.log"
    path.write_text("\n".join(lines(5)[:2] + ["", "   "] + lines(5)[2:]) + "\n", encoding="utf-8")

    assert producer.read_lines(path) == lines(5)
    assert producer.read_lines(path, limit=3) == lines(5)[:3]


def test_the_pace_keeps_to_the_rate(monkeypatch):
    slept = []
    monkeypatch.setattr(producer.time, "sleep", slept.append)
    monkeypatch.setattr(producer.time, "time", lambda: 100.0)

    producer.pace(100.0, 50, 100.0)     # 50 lines at 100/s should have taken 0.5 s, and no time has passed
    producer.pace(100.0, 50, 0)         # no limit
    producer.pace(90.0, 50, 100.0)      # already late

    assert slept == [pytest.approx(0.5)]


def test_the_settings(monkeypatch):
    values = {"KAFKA_BROKER": "kafka:9092", "KAFKA_TOPIC": "system-logs", "DATASET_FILE": "bgl-replay.log", "KAFKA_SSL_CA_FILE": "/ca",
              "KAFKA_SSL_CERT_FILE": "/c", "KAFKA_SSL_KEY_FILE": "/k", "PRODUCER_METRICS_PORT": "8001"}

    for name, value in values.items():
        monkeypatch.setenv(name, value)

    for name in ("LINES_PER_SECOND", "MAX_ROWS", "FLUSH_EVERY_ROWS", "RUN_ID"):
        monkeypatch.delenv(name, raising=False)

    settings = producer.Settings()
    assert settings.lines_per_second == 0 and settings.limit is None and settings.flush_every == 500 and settings.run.startswith("run-")

    monkeypatch.setenv("RUN_ID", "my-run")
    monkeypatch.setenv("MAX_ROWS", "100")
    monkeypatch.setenv("LINES_PER_SECOND", "250")
    settings = producer.Settings()
    assert (settings.run, settings.limit, settings.lines_per_second) == ("my-run", 100, 250.0)

    monkeypatch.delenv("DATASET_FILE")

    with pytest.raises(SystemExit, match="missing required setting: DATASET_FILE"):
        producer.Settings()
