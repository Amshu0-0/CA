# scripts/crash-test.py against a stand-in cluster: when the processor is killed (after the replay has started and the processor has stored lines, and
# never after the replay is over), how it is killed, what the script waits for, and when it says FAIL. The real cluster is not tested here.
#
# Why the script exists: on the first real run the processor was deleted by hand after the replay had already ended (a replay takes about a
# minute and a half), so nothing had been interrupted.

import importlib.util
import time
from pathlib import Path

import pytest

import nlplib as lib
import results

PIPELINE = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("crash_test", PIPELINE / "scripts" / "crash-test.py")
crash = importlib.util.module_from_spec(spec)
spec.loader.exec_module(crash)


def pod(name):
    return {"metadata": {"name": name}, "status": {"conditions": [{"type": "Ready", "status": "True"}]}}


class Cluster:
    # `stored`: what the REST API says on each call, as (lines stored, status); the last one repeats. `new_pod_after`: how many polls the new pod needs.
    def __init__(self, monkeypatch, stored, new_pod_after=2, group=((0, 7413, 15602, 8189),), problems=(), results_code=0, processors=("processor-old",), run_name_after=1, old_lingers=0):
        self.stored, self.events, self.commands = list(stored), [], []
        self.killed, self.polls, self.new_pod_after = False, 0, new_pod_after
        self.processors, self.group, self.problems, self.results_code = list(processors), group, list(problems), results_code
        self.run_name_polls, self.run_name_after = 0, run_name_after
        self.results_arguments, self.old_lingers = None, old_lingers

        monkeypatch.setattr(time, "sleep", lambda seconds: None)
        monkeypatch.setattr(lib, "stack_problems", lambda: self.problems)
        monkeypatch.setattr(lib, "pipeline_pods", self.pipeline_pods)
        monkeypatch.setattr(lib, "kubectl", self.kubectl)
        monkeypatch.setattr(lib, "kafka_group_lag", self.kafka_group_lag)
        monkeypatch.setattr(results, "run_of_job", self.run_of_job)
        monkeypatch.setattr(results, "find_run", self.find_run)
        monkeypatch.setattr(results, "main", self.results_main)

    def pipeline_pods(self, name):
        assert name == "processor"

        if not self.killed:
            return [pod(p) for p in self.processors]

        self.polls += 1

        # a pod that was killed can still be listed as ready for a moment
        if self.polls <= self.old_lingers:
            return [pod(p) for p in self.processors]

        return [pod("processor-new")] if self.polls > self.new_pod_after + self.old_lingers else []

    def kubectl(self, *args, input=None, timeout=60):
        self.commands.append(args)

        if "job" in args and "delete" in args:
            self.events.append("delete-job")
        elif args[0] == "apply":
            self.events.append("start-job")
        elif "pod" in args and "delete" in args:
            self.killed = True
            self.events.append("kill")

        return 0, "", ""

    def run_of_job(self, job):
        self.run_name_polls += 1

        if self.run_name_polls <= self.run_name_after:
            raise lib.Fail("could not read the log of the Job " + job)

        return "run-20261114-100000"

    def find_run(self, name):
        state = self.stored.pop(0) if len(self.stored) > 1 else self.stored[0]
        self.events.append("stored:" + str(state[0] if state else None))

        return None if state is None else {"run": name, "lines_stored": state[0], "status": state[1], "windows_stored": 0}

    def kafka_group_lag(self, group):
        if isinstance(self.group, Exception):
            raise self.group

        return self.group

    def results_main(self, argv):
        self.events.append("results")
        self.results_arguments = list(argv)
        print("Replay run-20261114-100000   status: complete")
        print("  log lines: 30000")
        print("The cluster and your computer (x.json) give the same numbers for every detector.")
        print()
        print("RESULT: PASS - the replay run-20261114-100000 is complete" if self.results_code == 0 else "RESULT: FAIL\n  - windows: the NLP code expects 394, the cluster has 396")

        return self.results_code


def run(cluster, capsys, *argv):
    code = crash.main(list(argv))

    return code, capsys.readouterr().out


def test_the_processor_is_killed_in_the_middle_of_the_replay_and_the_result_is_compared(monkeypatch, capsys, tmp_path):
    local = tmp_path / "local.json"
    local.write_text("{}")
    cluster = Cluster(monkeypatch, [None, (900, "running"), (4100, "running"), (8150, "running")])
    code, out = run(cluster, capsys, "--compare", str(local), "--json", str(tmp_path / "after.json"))

    assert code == 0, out

    # the order: the old Job goes, the replay starts, the processor stores lines, THEN it is killed, and only then the result is read
    assert cluster.events[:2] == ["delete-job", "start-job"]
    assert cluster.events.index("kill") > cluster.events.index("stored:8150")
    assert cluster.events.index("stored:4100") < cluster.events.index("kill") < cluster.events.index("results")
    assert "stored:8150" in cluster.events and cluster.events.count("kill") == 1

    # the kill: that pod, at once, with no goodbye
    (kill,) = [c for c in cluster.commands if "pod" in c and "delete" in c]
    assert "processor-old" in kill and "--grace-period=0" in kill and "--force" in kill

    # the result is read for the replay that was interrupted, compared with the file, and saved
    assert cluster.results_arguments == ["--run", "run-20261114-100000", "--wait", "1800", "--compare", str(local), "--json", str(tmp_path / "after.json")]

    assert "killed processor-old with no warning" in out and "stored 8150 lines" in out
    assert "processor-new is ready" in out
    assert "committed up to offset 7413" in out and "up to offset 15602" in out
    assert out.splitlines()[-1].startswith("RESULT: PASS - the processor was killed after 8150 lines of run-20261114-100000")


def test_there_is_only_one_result_line_and_the_one_of_results_py_is_renamed(monkeypatch, capsys):
    cluster = Cluster(monkeypatch, [(9000, "running")])
    code, out = run(cluster, capsys, "--compare", "does-not-exist.json")

    assert code == 0, out
    assert [line for line in out.splitlines() if line.startswith("RESULT:")] == [out.splitlines()[-1]]
    assert "Comparison: PASS - the replay run-20261114-100000 is complete" in out


def test_a_compare_file_that_does_not_exist_is_not_passed_on(monkeypatch, capsys, tmp_path):
    cluster = Cluster(monkeypatch, [(9000, "running")])
    code, out = run(cluster, capsys, "--compare", str(tmp_path / "nope.json"), "--json", "")

    assert code == 0, out
    assert cluster.results_arguments == ["--run", "run-20261114-100000", "--wait", "1800"]


def test_the_kill_point_can_be_chosen(monkeypatch, capsys):
    cluster = Cluster(monkeypatch, [(100, "running"), (11999, "running"), (12010, "running")])
    code, out = run(cluster, capsys, "--kill-after", "12000", "--compare", "")

    assert code == 0, out
    assert cluster.events.index("kill") > cluster.events.index("stored:12010")
    assert "stored:11999" in cluster.events and cluster.events.index("stored:11999") < cluster.events.index("kill")


def test_a_replay_that_is_already_over_is_never_killed(monkeypatch, capsys):
    cluster = Cluster(monkeypatch, [(500, "running"), (30000, "complete")])
    code, out = run(cluster, capsys)

    assert code == 1
    assert "kill" not in cluster.events and "results" not in cluster.events
    assert "was already complete" in out and "nothing left to interrupt" in out
    assert out.splitlines()[-1].startswith("RESULT: FAIL")


def test_a_processor_that_stores_nothing_is_not_killed_either(monkeypatch, capsys):
    cluster = Cluster(monkeypatch, [None])

    # the clock: the script gives up after its patience, and the stand-in clock runs fast
    clock = iter(range(0, 100000, 50))
    monkeypatch.setattr(time, "monotonic", lambda: next(clock))
    code, out = run(cluster, capsys)

    assert code == 1
    assert "kill" not in cluster.events
    assert "the processor had stored only 0 lines" in out or "stored only 0 lines" in out
    assert out.splitlines()[-1].startswith("RESULT: FAIL")


def test_a_stack_that_is_not_ready_stops_the_test_before_anything_is_started(monkeypatch, capsys):
    cluster = Cluster(monkeypatch, [(9000, "running")], problems=["no processor pod is ready"])
    code, out = run(cluster, capsys)

    assert code == 1
    assert cluster.commands == [] and cluster.events == []
    assert "no processor pod is ready" in out


def test_two_processor_pods_stop_the_test_before_anything_is_started(monkeypatch, capsys):
    cluster = Cluster(monkeypatch, [(9000, "running")], processors=("processor-a", "processor-b"))
    code, out = run(cluster, capsys)

    assert code == 1
    assert cluster.events == []
    assert "exactly one ready processor pod, found 2" in out


def test_a_producer_that_never_starts_is_reported(monkeypatch, capsys):
    cluster = Cluster(monkeypatch, [(9000, "running")], run_name_after=10 ** 9)
    clock = iter(range(0, 100000, 30))
    monkeypatch.setattr(time, "monotonic", lambda: next(clock))
    code, out = run(cluster, capsys)

    assert code == 1
    assert "did not start the replay within 4 minutes" in out
    assert "kill" not in cluster.events


def test_a_new_pod_that_never_gets_ready_is_reported(monkeypatch, capsys):
    cluster = Cluster(monkeypatch, [(9000, "running")], new_pod_after=10 ** 9)
    clock = iter(range(0, 100000, 40))
    monkeypatch.setattr(time, "monotonic", lambda: next(clock))
    code, out = run(cluster, capsys)

    assert code == 1
    assert "no new processor pod was ready 10 minutes after the kill" in out
    assert "results" not in cluster.events


def test_a_result_that_differs_from_the_uninterrupted_replay_is_a_failure(monkeypatch, capsys):
    cluster = Cluster(monkeypatch, [(9000, "running")], results_code=1)
    code, out = run(cluster, capsys, "--compare", "")

    assert code == 1
    assert "Comparison: FAIL" in out and "windows: the NLP code expects 394, the cluster has 396" in out
    assert "not the one of an uninterrupted replay" in out
    assert out.splitlines()[-1].startswith("RESULT: FAIL")
    assert not any(line.startswith("RESULT: PASS") for line in out.splitlines())


@pytest.mark.parametrize("answer", [None, lib.Fail("no kafka tool"), ((0, None, None, None),)], ids=["no rows", "kafka tool failed", "dashes"])
def test_a_consumer_group_that_cannot_be_read_does_not_stop_the_test(monkeypatch, capsys, answer):
    cluster = Cluster(monkeypatch, [(9000, "running")], group=answer)
    code, out = run(cluster, capsys, "--compare", "")

    assert code == 0, out
    assert "committed up to offset" not in out
    assert "killed processor-old" in out


def test_the_default_files_are_the_evidence_files(monkeypatch, capsys):
    cluster = Cluster(monkeypatch, [(9000, "running")])
    code, out = run(cluster, capsys)

    assert code == 0, out
    assert cluster.results_arguments[-2:] == ["--json", "evidence/cluster-summary-after-crash.json"]


def test_the_killed_pod_that_is_still_listed_for_a_moment_is_not_taken_for_the_new_one(monkeypatch, capsys):
    cluster = Cluster(monkeypatch, [(9000, "running")], new_pod_after=1, old_lingers=3)
    code, out = run(cluster, capsys, "--compare", "")

    assert code == 0, out
    assert "processor-new is ready" in out
    assert "processor-old is ready" not in out
