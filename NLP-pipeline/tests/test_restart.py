# The processor can die at any moment (a node goes down, a pod is moved, the out-of-memory killer strikes). After it starts again, the
# MongoDB documents must be exactly what an uninterrupted run would have made: no window missing, none twice, none different.

import random

import pytest

from engine import Engine
from fakes import STEPS, Broker, Crash, FakeDetectors, comparable, live, make_records, record
from sink import MemorySink


def uninterrupted(records, sizes):
    broker, sink = Broker(records), MemorySink()
    live(broker, sink, FakeDetectors(), sizes)
    return broker, sink


def with_crashes(records, sizes, crashes):
    broker, sink = Broker(records), MemorySink()

    for crash_at in crashes:
        with pytest.raises(Crash):
            live(broker, sink, FakeDetectors(), sizes, crash_at)

    live(broker, sink, FakeDetectors(), sizes)
    return broker, sink


def test_an_uninterrupted_replay_makes_events_windows_and_a_finished_run():
    records = make_records(1, n=300)
    broker, sink = uninterrupted(records, [25])
    events, windows, runs = comparable(sink)

    assert len(events) == 300
    assert len(windows) > 10
    assert broker.committed == records[-1].offset + 1

    run = runs["r1"]
    assert run["status"] == "complete" and run["done"] is True and run["expected_lines"] == 300
    assert run["started_at"] == 1000.0 and run["finished_at"] == 1000.0

    # every line is in at least one window, the numbers inside a window agree with its lines
    seen = set()

    for window in windows.values():
        inside = [e for e in events.values() if window["start"] <= e["ts"] < window["end"]]
        assert window["n_messages"] == len(inside)
        assert window["truth_alerts"] == sum(e["alert"] for e in inside)
        assert window["truth"] == int(window["truth_alerts"] > 0)
        assert window["keyword_hits"] == sum(e["keyword"] for e in inside)
        assert window["llm_hits"] == sum(1 for e in inside if e["llm"] == 1)
        assert window["first_offset"] == min(e["offset"] for e in inside) and window["last_offset"] == max(e["offset"] for e in inside)
        seen.update(e["offset"] for e in inside)

    assert seen == {e["offset"] for e in events.values()}


def test_the_batch_size_does_not_change_the_result():
    records = make_records(2, n=250)
    reference = comparable(uninterrupted(records, [1])[1])

    for sizes in ([3], [7, 1, 50], [250], [1000], [2, 300]):
        assert comparable(uninterrupted(records, sizes)[1]) == reference, sizes


def test_a_crash_after_any_step_of_any_batch_changes_nothing():
    for seed in (3, 4):
        records = make_records(seed, n=120)
        sizes = [9, 4, 17]
        broker, sink = uninterrupted(records, sizes)
        reference = comparable(sink)

        # one commit per batch
        for number in range(len(broker.commits)):
            for step in STEPS:
                _, sink = with_crashes(records, sizes, [(number, step)])
                assert comparable(sink) == reference, (seed, number, step)


def test_several_crashes_in_a_row_change_nothing():
    rng = random.Random(5)

    for case in range(60):
        records = make_records(100 + case, n=rng.randrange(1, 150))
        sizes = [rng.choice([1, 2, 5, 11, 40]) for _ in range(3)]
        reference = comparable(uninterrupted(records, sizes)[1])
        crashes = [(rng.randrange(0, 6), rng.choice(STEPS)) for _ in range(rng.randrange(1, 4))]

        # a crash planned for a batch that does not exist is no crash: only the ones that happen are checked
        broker, sink = Broker(records), MemorySink()

        for crash_at in crashes:
            try:
                live(broker, sink, FakeDetectors(), sizes, crash_at)
            except Crash:
                pass

        live(broker, sink, FakeDetectors(), sizes)
        assert comparable(sink) == reference, (case, sizes, crashes)


def test_the_commit_never_goes_past_a_line_that_an_open_window_still_needs():
    records = make_records(6, n=200)
    broker = Broker(records)
    engine = Engine(FakeDetectors(), MemorySink().load_state, lambda: 0.0)
    sink = MemorySink()

    for start in range(0, 200, 10):
        batch = records[start: start + 10]
        sink.write(engine.process(batch))
        held = engine.windower.oldest_offset()
        committed = engine.commit_offsets(batch)[0]

        if held is None:
            assert committed == batch[-1].offset + 1
        else:
            assert committed == min(held, batch[-1].offset + 1)
            assert all(i.offset >= committed for i in engine.windower.items)


def test_messages_that_kafka_delivers_again_are_skipped():
    records = make_records(7, n=80)
    engine = Engine(FakeDetectors(), MemorySink().load_state, lambda: 0.0)
    first = engine.process(records[:40])
    again = engine.process(records[20:60])

    assert first.counts["lines"] == 40
    assert again.counts["redelivered"] == 20 and again.counts["lines"] == 20


def test_a_new_run_closes_the_old_one_even_without_its_end_marker():
    one = make_records(8, n=60, run="r1", end=False)
    two = make_records(9, n=60, run="r2", first_offset=60)
    records = one + two
    broker, sink = uninterrupted(records, [7])
    reference = comparable(sink)
    _, windows, runs = reference

    assert runs["r1"]["status"] == "superseded" and runs["r1"]["done"] is True
    assert runs["r2"]["status"] == "complete"
    assert {w["run"] for w in windows.values()} == {"r1", "r2"}

    # a window of the first run never holds a line of the second
    assert max(w["last_offset"] for w in windows.values() if w["run"] == "r1") < 60

    for number in range(len(broker.commits)):
        for step in STEPS:
            _, sink = with_crashes(records, [7], [(number, step)])
            assert comparable(sink) == reference, (number, step)


def test_smoke_lines_are_stored_with_their_trace_id_and_do_not_enter_a_window():
    from fakes import bgl_line

    engine = Engine(FakeDetectors(), MemorySink().load_state, lambda: 0.0)
    out = engine.process([record(0, {"kind": "smoke", "trace_id": "abc-123", "line": bgl_line(5000, 11, "KERNDTLB")})])

    assert out.windows == [] and engine.run is None and out.runs == {}
    (event,) = out.events
    assert event["trace_id"] == "abc-123" and event["smoke"] is True and event["alert"] == 1 and event["_id"] == "smoke:0"
    assert event["template_id"] == 11 and event["llm"] == 1
    assert out.logs[0][1] == "stored" and out.logs[0][2]["trace_id"] == "abc-123"


def test_bad_messages_are_counted_and_skipped_and_do_not_stop_the_rest():
    from fakes import bgl_line

    good = {"kind": "log", "run": "r1", "seq": 0, "line": bgl_line(1000, 3)}
    bad = [
        "not even a dictionary",
        {"kind": "something else"},
        {"kind": "log", "run": "r1", "line": "too short"},
        {"kind": "log", "line": bgl_line(1000, 3)},
        {"kind": "smoke", "line": bgl_line(1000, 3)},
        {"kind": "end"},
    ]
    engine = Engine(FakeDetectors(), MemorySink().load_state, lambda: 0.0)
    out = engine.process([record(i, value) for i, value in enumerate(bad + [good])])

    assert out.counts["bad"] == len(bad) and out.counts["lines"] == 1 and len(out.events) == 1
    assert 1 <= len([entry for entry in out.logs if entry[0] == "warning"]) <= 3


def test_a_run_whose_replay_is_over_ignores_lines_that_come_after_its_end():
    records = make_records(10, n=50)
    extra = make_records(11, n=5, first_offset=records[-1].offset + 1, end=False)
    broker, sink = uninterrupted(records + extra, [10])
    events, windows, runs = comparable(sink)

    assert len(events) == 55 and runs["r1"]["done"] is True
    assert max(w["last_offset"] for w in windows.values()) <= 49


def test_a_replay_that_ended_normally_keeps_its_status_when_the_next_one_starts():
    records = make_records(12, n=60, run="r1") + make_records(13, n=60, run="r2", first_offset=61)
    broker, sink = uninterrupted(records, [9])
    reference = comparable(sink)

    assert reference[2]["r1"]["status"] == "complete" and reference[2]["r2"]["status"] == "complete"

    for number in range(len(broker.commits)):
        for step in STEPS:
            _, crashed = with_crashes(records, [9], [(number, step)])
            assert comparable(crashed) == reference, (number, step)
