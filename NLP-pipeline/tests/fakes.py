# Stand-ins and helpers for the tests: a fake set of detectors (instant, no model), a fake Kafka (the records of one partition and the
# committed offset) and a loop that does what processor.py does: read a batch, process it, write events, windows and runs, then commit.

import random

from engine import Engine, Line, Record, Score

TOPIC = "system-logs"


class Crash(Exception):
    """The processor dies at this very moment."""


# Times in seconds with ties (30%), small steps, and now and then a gap longer than a window.
def random_times(rng, n):
    times, now = [], rng.randrange(1_000_000, 2_000_000)

    for _ in range(n):
        kind = rng.random()
        now += 0 if kind < 0.3 else rng.randrange(1, 40) if kind < 0.85 else rng.randrange(40, 2000)
        times.append(now)

    return times


# Detectors that need no files: the template is the number after "t" in the text, "keyword" says yes for multiples of 7, the saved LLM
# decision says yes for multiples of 11 and "nothing saved" for multiples of 13, LogBERT flags a window that holds a multiple of 5.
class FakeDetectors:
    size_seconds = 300
    step_seconds = 60

    def __init__(self):
        self.scored = 0

    def classify(self, content):
        template_id = int(content.split()[0][1:])
        keyword = int(template_id % 7 == 0)
        llm = None if template_id % 13 == 0 else int(template_id % 11 == 0)
        return Line(template_id, f"template {template_id}", keyword, 1 if keyword else None, "fake", llm)

    def score(self, template_ids):
        self.scored += 1
        missed = sum(1 for i in template_ids if i % 5 == 0)
        return Score(int(missed > 2), missed, 1)


def bgl_line(ts, template_id, tag="-", note=""):
    return f"{tag} {ts} 2005.06.03 R02-M1-N0-C:J12-U11 2005-06-03-15.42.50.000000 R02-M1-N0-C:J12-U11 RAS KERNEL INFO t{template_id} {note}".rstrip()


def record(offset, value, partition=0):
    return Record(TOPIC, partition, offset, 1_700_000_000_000 + offset, value)


# The messages of one replay: n log lines, sorted by time as the export script makes them, and the end marker.
def make_records(seed, n=300, run="r1", first_offset=0, end=True):
    rng = random.Random(seed)
    records = []

    for seq, ts in enumerate(random_times(rng, n)):
        tag = "KERNDTLB" if rng.random() < 0.05 else "-"
        line = bgl_line(ts, rng.randrange(1, 30), tag, f"line {seq}")
        records.append(record(first_offset + seq, {"kind": "log", "run": run, "seq": seq, "line": line}))

    if end:
        records.append(record(first_offset + n, {"kind": "end", "run": run, "lines": n}))

    return records


# What Kafka remembers for one partition.
class Broker:
    def __init__(self, records):
        self.records = records
        self.committed = records[0].offset if records else 0
        self.commits = []


# One life of the processor, as processor.py lives it: start, read from the committed offset, and for every batch write events, windows
# and runs, and then commit. crash_at = (batch number in this life, step) kills it right after that step. Returns the engine.
def live(broker, sink, detectors, sizes, crash_at=None, clock=lambda: 1000.0):
    engine = Engine(detectors, sink.load_state, clock)
    first = broker.records[0].offset
    position = broker.committed - first
    number = 0

    def step(name):
        if crash_at == (number, name):
            raise Crash(f"crashed after {name} of batch {number}")

    while position < len(broker.records):
        batch = broker.records[position: position + sizes[number % len(sizes)]]
        position += len(batch)

        out = engine.process(batch)
        step("processed")
        sink.write_events(out.events)
        step("events")
        sink.write_windows(out.windows)
        step("windows")
        sink.write_runs(out.runs)
        step("runs")

        for partition, offset in engine.commit_offsets(batch).items():
            assert partition == 0
            assert offset >= broker.committed, "a commit must never go backwards"
            broker.committed = offset
            broker.commits.append(offset)

        step("committed")
        number += 1

    return engine


STEPS = ("processed", "events", "windows", "runs", "committed")


# The documents without the timing numbers (they differ from run to run by nature).
def comparable(sink):
    def clean(document):
        return {key: value for key, value in document.items() if key != "score_ms"}

    return ({k: clean(v) for k, v in sink.events.items()}, {k: clean(v) for k, v in sink.windows.items()}, {k: clean(v) for k, v in sink.runs.items()})


# mongomock (the fake MongoDB of the tests) cannot run the bulk operations of the newest pymongo. This runs them one by one, with the very
# filter, document and upsert flag that MongoSink put into them, so what MongoSink asks MongoDB to do is still what the tests check.
class ShimCollection:
    def __init__(self, collection):
        self.collection = collection

    def __getattr__(self, name):
        return getattr(self.collection, name)

    def bulk_write(self, operations, ordered=True):
        for operation in operations:
            kind = type(operation).__name__

            if kind == "ReplaceOne":
                self.collection.replace_one(operation._filter, operation._doc, upsert=operation._upsert)
            elif kind == "UpdateOne":
                self.collection.update_one(operation._filter, operation._doc, upsert=operation._upsert)
            else:
                raise AssertionError(f"MongoSink must only use ReplaceOne and UpdateOne, not {kind}")


class ShimDatabase:
    def __init__(self, database):
        self.database = database

    def __getitem__(self, name):
        return ShimCollection(self.database[name])
