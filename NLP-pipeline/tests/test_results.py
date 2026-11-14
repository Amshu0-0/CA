# scripts/results.py and scripts/tables.py against a stand-in REST API and a stand-in kubectl: how the replay is found, how the waiting works,
# what is printed, and when the script says FAIL. (The real REST API is tested in test_restapi.py, the real cluster is not tested here.)

import json
from types import SimpleNamespace

import pytest

import nlplib
import results
import tables
from summary import summarize

LOG = "\n".join([
    "[pod/producer-replay-abc/producer] not json at all",
    '[pod/producer-replay-abc/producer] {"ts":"2026-11-14T09:15:00.000+00:00","level":"info","service":"producer","msg":"producer started","run":"run-old","lines_in_file":10}',
    '{"ts":"2026-11-14T09:16:00.000+00:00","level":"info","service":"producer","msg":"progress","run":"run-new","lines_sent":500}',
    '{"ts":"2026-11-14T09:16:01.000+00:00","level":"info","service":"producer","msg":"producer started","run":"run-new","lines_in_file":30000}',
])


def window(start, truth, logbert, llm, keyword, ms=10.0):
    return {"start": start, "truth": truth, "n_messages": 5, "truth_alerts": truth, "logbert": logbert, "llm": llm, "keyword": keyword, "logbert_missed": 9 * logbert, "score_ms": ms}


WINDOWS = [window(1000, 1, 1, 1, 1), window(1060, 1, 1, 0, 1), window(1120, 0, 1, 0, 0), window(1180, 0, 0, 0, 0), window(1240, 0, 0, 1, 0)]


def make_summary(status="complete", keyword_fp_extra=0):
    summary = {"run": {"run": "run-new", "status": status}, "lines": {"stored": 30, "with_alert_tag": 2, "template_not_known": 0, "llm_has_no_saved_decision": 3}, **summarize(WINDOWS)}
    summary["detectors"]["keyword"]["fp"] += keyword_fp_extra
    return summary


class Cluster:
    # `states`: what /runs says on each call (a row for run-new, or None for "not stored yet"); the last one repeats.
    def __init__(self, states, summary=None):
        self.states = list(states)
        self.summary = summary or make_summary()
        self.calls = []

    def rest_json(self, path, host=None, port=None, timeout=30):
        self.calls.append(path)

        if path.startswith("/runs"):
            state = self.states.pop(0) if len(self.states) > 1 else self.states[0]
            return 200, {"count": 0 if state is None else 1, "runs": [] if state is None else [state]}

        if path.startswith("/summary"):
            return 200, self.summary

        if path.startswith("/windows"):
            return 200, {"run": "run-new", "count": 2, "skip": 0, "windows": WINDOWS[:2]}

        return 404, {"error": "no such path"}


def row(status, lines, windows):
    return {"run": "run-new", "status": status, "lines_stored": lines, "windows_stored": windows}


@pytest.fixture
def world(monkeypatch):
    clock = SimpleNamespace(now=0.0)
    fake_time = SimpleNamespace(monotonic=lambda: clock.now, sleep=lambda seconds: setattr(clock, "now", clock.now + seconds))
    monkeypatch.setattr(results, "time", fake_time)
    monkeypatch.setattr(nlplib, "kubectl", lambda *args, **kwargs: (0, LOG, ""))

    def use(cluster):
        monkeypatch.setattr(nlplib, "rest_json", cluster.rest_json)
        return cluster

    return SimpleNamespace(use=use, clock=clock, monkeypatch=monkeypatch)


def test_the_replay_of_the_job_is_the_last_one_its_producer_started(world):
    assert results.run_of_job() == "run-new"


def test_a_job_log_without_the_start_line_is_explained(world):
    world.monkeypatch.setattr(nlplib, "kubectl", lambda *args, **kwargs: (0, "waiting for kafka\n", ""))

    with pytest.raises(nlplib.Fail, match="no 'producer started' line"):
        results.run_of_job()


def test_it_waits_for_the_processor_and_then_prints_the_table(world, capsys):
    states = [None] + [row("running", 500, 3)] * 5 + [row("running", 20000, 400)] * 5 + [row("complete", 30000, 900)]
    cluster = world.use(Cluster(states))

    assert results.main(["--replay-job", "--wait", "600"]) == 0
    shown = capsys.readouterr().out

    assert "Waiting for the processor to finish the replay run-new" in shown
    assert "the processor has not stored anything of run-new yet" in shown
    assert "running: 500 lines and 3 windows stored so far" in shown
    assert "Replay run-new   status: complete" in shown
    assert "keyword rules (B4)" in shown and "RESULT: PASS" in shown
    assert any(call.startswith("/summary?run=run-new") for call in cluster.calls)


def test_without_a_name_the_newest_replay_is_shown(world, capsys):
    cluster = world.use(Cluster([row("complete", 30, 5)]))

    assert results.main([]) == 0
    assert "Replay run-new" in capsys.readouterr().out and "/runs?limit=1" in cluster.calls


def test_a_replay_that_is_not_complete_says_so(world, capsys):
    world.use(Cluster([row("running", 10, 1)], make_summary(status="running")))

    assert results.main(["--run", "run-new"]) == 0
    assert "The replay is not complete" in capsys.readouterr().out


def test_no_replay_at_all_points_to_make_replay(world, capsys):
    world.use(Cluster([None]))

    assert results.main([]) == 1
    assert "make replay" in capsys.readouterr().out


def test_a_processor_that_stops_storing_is_reported_after_four_minutes(world, capsys):
    world.use(Cluster([row("running", 500, 3)]))

    assert results.main(["--run", "run-new", "--wait", "3600"]) == 1
    assert "nothing new was stored for 4 minutes" in capsys.readouterr().out


def test_the_waiting_ends_at_the_limit(world, capsys):
    states = [row("running", n, n) for n in range(1, 400)]
    world.use(Cluster(states))

    assert results.main(["--run", "run-new", "--wait", "60"]) == 1
    assert "was not finished after 60 seconds" in capsys.readouterr().out


def test_windows_with_alerts_are_listed(world, capsys):
    world.use(Cluster([row("complete", 30, 5)]))

    assert results.main(["--windows", "2"]) == 0
    shown = capsys.readouterr().out

    assert "The first 2 windows that hold an alert line" in shown and "1970-01-01 00:16" in shown


def test_the_cluster_is_compared_with_the_laptop(world, tmp_path, capsys):
    world.use(Cluster([row("complete", 30, 5)]))
    saved = tmp_path / "local.json"
    saved.write_text(json.dumps(make_summary()), encoding="utf-8")

    assert results.main(["--compare", str(saved)]) == 0
    assert "give the same numbers for every detector" in capsys.readouterr().out

    # a difference in LogBERT alone is explained and tolerated; one in the keyword rules is a failure
    other = make_summary()
    other["detectors"]["logbert"]["tp"] += 1
    saved.write_text(json.dumps(other), encoding="utf-8")

    assert results.main(["--compare", str(saved)]) == 0
    assert "LogBERT can differ by a window or two" in capsys.readouterr().out

    saved.write_text(json.dumps(make_summary(keyword_fp_extra=3)), encoding="utf-8")

    assert results.main(["--compare", str(saved)]) == 1
    assert "keyword rules (B4): fp laptop" in capsys.readouterr().out


def test_the_windows_of_the_nlp_code_are_compared_with_the_cluster(world, tmp_path, capsys):
    world.use(Cluster([row("complete", 30, 5)]))
    bundle = results.Path(results.__file__).resolve().parent.parent / "bundle"
    info_file = bundle / "replay-info.json"
    existed = info_file.exists()
    before = info_file.read_text(encoding="utf-8") if existed else None
    bundle.mkdir(exist_ok=True)

    try:
        info_file.write_text(json.dumps({"lines": 30, "alert_lines": 2, "windows": 5, "anomalous_windows": 2}), encoding="utf-8")
        assert results.main([]) == 0
        assert "built exactly the windows that the NLP code expects" in capsys.readouterr().out

        info_file.write_text(json.dumps({"lines": 30, "alert_lines": 2, "windows": 6, "anomalous_windows": 2}), encoding="utf-8")
        assert results.main([]) == 1
        assert "windows: the NLP code expects 6, the cluster has 5" in capsys.readouterr().out
    finally:
        if existed:
            info_file.write_text(before, encoding="utf-8")
        else:
            info_file.unlink()


def test_a_cluster_that_does_not_answer_is_reported_with_the_address_hint(world, capsys):
    world.monkeypatch.setattr(nlplib, "rest_get", lambda *a, **k: (None, "TimeoutError: timed out"))
    world.monkeypatch.setattr(nlplib, "rest_target", lambda host=None, port=None: ("203.0.113.9", 30080))

    assert results.main([]) == 1
    assert "did not answer" in capsys.readouterr().out
