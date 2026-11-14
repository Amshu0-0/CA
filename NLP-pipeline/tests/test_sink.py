# The two sinks must do the same: what the tests prove with MemorySink (crash at every step) holds for MongoSink only if both leave the
# same documents behind.

import mongomock

from engine import Engine
from fakes import Broker, FakeDetectors, ShimDatabase, comparable, live, make_records
from sink import MemorySink, MongoSink, run_fields


def mongo_sink():
    return MongoSink(ShimDatabase(mongomock.MongoClient()["nlp"]))


def documents(sink):
    return {name: {d["_id"]: d for d in getattr(sink, name).find({})} for name in ("events", "windows", "runs")}


def test_both_sinks_leave_the_same_documents_behind():
    records = make_records(1, n=250) + make_records(2, n=80, run="r2", first_offset=251, end=False)
    memory, mongo = MemorySink(), mongo_sink()

    for sink in (memory, mongo):
        live(Broker(records), sink, FakeDetectors(), [30])

    # (without the timing numbers: they differ from run to run by nature)
    def untimed(documents):
        return {key: {k: v for k, v in document.items() if k != "score_ms"} for key, document in documents.items()}

    found = documents(mongo)
    assert untimed(found["events"]) == untimed(memory.events) and untimed(found["windows"]) == untimed(memory.windows) and found["runs"] == memory.runs
    assert len(found["events"]) == 330 and found["runs"]["r1"]["status"] == "complete" and found["runs"]["r2"]["status"] == "running"


def test_writing_a_batch_twice_changes_nothing():
    records = make_records(2, n=100)

    for sink in (MemorySink(), mongo_sink()):
        engine = Engine(FakeDetectors(), sink.load_state, lambda: 1.0)
        out = engine.process(records)
        sink.write(out)
        first = documents(sink) if isinstance(sink, MongoSink) else {"events": dict(sink.events), "windows": dict(sink.windows), "runs": dict(sink.runs)}
        sink.write(out)     # the same output again, as after a crash that came between the write and the commit
        second = documents(sink) if isinstance(sink, MongoSink) else {"events": dict(sink.events), "windows": dict(sink.windows), "runs": dict(sink.runs)}

        assert first == second


def test_a_run_document_keeps_its_start_time_and_takes_the_new_state():
    for sink in (MemorySink(), mongo_sink()):
        sink.write_runs({"r": {"set": {"next_k": 1}, "insert": {"status": "running", "started_at": 10.0}}})
        sink.write_runs({"r": {"set": {"next_k": 5, "status": "complete"}, "insert": {"status": "running", "started_at": 99.0}}})

        document = sink.runs["r"] if isinstance(sink, MemorySink) else sink.runs.find_one({"_id": "r"})
        assert document == {"_id": "r", "status": "complete", "started_at": 10.0, "next_k": 5}
        assert sink.load_state("r") == {"next_k": 5}
        assert sink.load_state("unknown") is None


def test_a_field_is_never_in_both_parts_of_an_update():
    to_set, to_insert = run_fields({"set": {"status": "complete", "a": 1}, "insert": {"status": "running", "b": 2}})
    assert to_set == {"status": "complete", "a": 1} and to_insert == {"b": 2}
