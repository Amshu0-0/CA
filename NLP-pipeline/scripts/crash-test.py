# Kills the processor in the middle of a replay and checks that the finished result is the same as without the crash.
#
#   make crash-test
#   make crash-test CRASH_ARGS="--kill-after 12000"
#
# This is the two-window test of the README, with the kill at the right moment and made by the program. A replay is over in about a minute and a
# half, which is too fast to kill by hand: the first try on the real cluster came after the replay had ended and proved nothing.
#
#   1. Starts a replay (deletes the old Job producer-replay, applies producer/job-replay.yaml) and finds its name.
#   2. Asks the REST API how many lines of it the processor has stored, until the number reaches --kill-after (default 8000 of 30000).
#   3. Kills the processor pod at once: "kubectl delete pod --grace-period=0 --force". The process gets no time to finish its batch or to say
#      goodbye to Kafka, which is what a crash looks like.
#   4. Waits for the new processor pod. It loads the models and reads from the offset that the old one committed last, while the producer goes on
#      sending (Kafka holds the lines).
#   5. Waits until the replay is complete and prints the table of  make results  (scripts/results.py), compared with your computer.
#
# It passes if that table is the one of a replay that was not interrupted: no window missing, none twice, the same verdicts for every detector.
# Why that must hold: the processor commits a Kafka offset only after the documents are written, and never past the oldest line that an open
# window still needs; the documents have fixed ids, so writing them twice changes nothing; LogBERT's masks are seeded by the position of the chunk.
# tests/test_restart.py checks this logic against fakes of Kafka and MongoDB; this checks it on the cluster.

import argparse
import contextlib
import sys
import time
import traceback
from pathlib import Path

import nlplib as lib
import results

JOB = "producer-replay"


def say(text=""):
    print(text, flush=True)


# Stands in for the standard output while scripts/results.py runs: every line is printed at once, and its line "RESULT: ..." becomes
# "Comparison: ..." because this script prints the one RESULT line.
class Relabel:
    def __init__(self, real):
        self.real, self.pending = real, ""

    def write(self, text):
        self.pending += text

        while "\n" in self.pending:
            line, self.pending = self.pending.split("\n", 1)
            self.real.write(("Comparison: " + line[len("RESULT: "):] if line.startswith("RESULT: ") else line) + "\n")

        self.real.flush()
        return len(text)

    def flush(self):
        self.real.flush()


def ready_processors():
    return [p for p in lib.pipeline_pods("processor") if lib.pod_is_ready(p)]


# Start a replay and return its name (the producer writes it into its log when it starts).
def start_replay():
    code, out, err = lib.kubectl("-n", lib.NS, "delete", "job", JOB, "--ignore-not-found", "--wait=true", timeout=180)

    if code != 0:
        raise lib.Fail("could not delete the old Job " + JOB + ": " + (err.strip() or out.strip() or "no message"))

    code, out, err = lib.kubectl("apply", "-f", str(lib.ROOT / "producer" / "job-replay.yaml"))

    if code != 0:
        raise lib.Fail("could not start the Job " + JOB + ": " + (err.strip() or out.strip() or "no message"))

    def started():
        try:
            return results.run_of_job(JOB)
        except lib.Fail:
            return None  # the pod does not exist yet, or has not printed its first line

    name, seconds = lib.wait_for(started, 240, interval=2)

    if not name:
        raise lib.Fail("the producer did not start the replay within 4 minutes. Run:  make diagnose")

    return name


# Wait until the processor has stored this many lines of the replay. Returns the number it had. A replay that is already over cannot be interrupted.
def wait_until_stored(name, wanted, patience=300):
    start, last_print = time.monotonic(), None

    while True:
        run = results.find_run(name)
        stored, status = (0, None) if run is None else (run["lines_stored"], run["status"])

        if status in ("complete", "superseded"):
            raise lib.Fail("the replay " + name + " was already " + status + " when the processor had stored " + str(stored) + " lines, so there was nothing left to interrupt. "
                           "Run it again with a smaller --kill-after (now " + str(wanted) + ")")

        if stored >= wanted:
            return stored

        if last_print is None or time.monotonic() - last_print >= 5:
            say("      lines stored so far: " + str(stored))
            last_print = time.monotonic()

        if time.monotonic() - start > patience:
            raise lib.Fail("after " + str(patience) + " seconds the processor had stored only " + str(stored) + " lines of " + name + ". Is it alive?  make logs   (and  make status)")

        time.sleep(1)


def kill(pod):
    code, out, err = lib.kubectl("-n", lib.NS, "delete", "pod", pod, "--grace-period=0", "--force", "--wait=false", timeout=60)

    if code != 0:
        raise lib.Fail("could not kill the pod " + pod + ": " + (err.strip() or out.strip() or "no message"))


# Where the consumer group will start again: (committed offset, end of the topic), or None when Kafka does not say.
def committed_offset():
    try:
        rows = lib.kafka_group_lag("processor")
    except lib.Fail:
        return None

    if not rows or rows[0][1] is None or rows[0][2] is None:
        return None

    return rows[0][1], rows[0][2]


def main(argv=None):
    parser = argparse.ArgumentParser(description="Kill the processor in the middle of a replay and check that nothing is lost or changed.")
    parser.add_argument("--kill-after", type=int, default=8000, metavar="LINES", help="kill the processor when it has stored this many lines of the replay (default 8000 of 30000)")
    parser.add_argument("--wait", type=int, default=1800, metavar="SECONDS", help="wait up to this long for the replay to be complete after the kill (default 1800)")
    parser.add_argument("--compare", default="evidence/local-replay.json", metavar="FILE", help="compare with this summary of  make local-replay --save  (default evidence/local-replay.json, if it exists)")
    parser.add_argument("--json", default="evidence/cluster-summary-after-crash.json", metavar="FILE", help="save the summary of the cluster in this file")
    args = parser.parse_args(argv)

    try:
        say("Crash test: the processor is killed in the middle of a replay (namespace " + lib.NS + ")")

        say("\n[1/5] The stack is running")
        problems = lib.stack_problems()

        if problems:
            raise lib.Fail("the stack is not ready: " + ", ".join(problems) + ". Run:  make status")

        before = ready_processors()

        if len(before) != 1:
            raise lib.Fail("expected exactly one ready processor pod, found " + str(len(before)) + ". Run:  make status")

        old = before[0]["metadata"]["name"]
        say("      the processor pod is " + old)

        say("\n[2/5] Start a replay")
        name = start_replay()
        say("      the producer started the replay " + name + " (it sends the replay file to Kafka for about a minute)")

        say("\n[3/5] Wait until the processor has stored " + str(args.kill_after) + " lines of it, then kill it")
        stored = wait_until_stored(name, args.kill_after)
        killed_at = time.monotonic()
        kill(old)
        say("      killed " + old + " with no warning (--grace-period=0 --force) when it had stored " + str(stored) + " lines of " + name)
        offset = committed_offset()

        if offset:
            say("      the consumer group has committed up to offset " + str(offset[0]) + "; Kafka holds the topic up to offset " + str(offset[1]) + " (and the producer keeps sending)")
            say("      the new processor starts at " + str(offset[0]) + ": it reads those lines again, also the ones that the old pod had already stored, and writes them under the same ids")

        say("\n[4/5] The new processor pod loads the models and goes on")
        new, seconds = lib.wait_for(lambda: [p for p in ready_processors() if p["metadata"]["name"] != old], 600, interval=3)

        if not new:
            raise lib.Fail("no new processor pod was ready 10 minutes after the kill. Run:  make diagnose")

        say("      " + new[0]["metadata"]["name"] + " is ready " + str(round(time.monotonic() - killed_at)) + " s after the kill")

        say("\n[5/5] Wait for the end of the replay and compare it with your computer")
        arguments = ["--run", name, "--wait", str(args.wait)]

        if args.compare and Path(args.compare).is_file():
            arguments += ["--compare", args.compare]

        if args.json:
            arguments += ["--json", args.json]

        with contextlib.redirect_stdout(Relabel(sys.stdout)):
            code = results.main(arguments)

        if code != 0:
            raise lib.Fail("the finished replay is not the one of an uninterrupted replay (see the lines above)")

        say("\nRESULT: PASS - the processor was killed after " + str(stored) + " lines of " + name + "; a new pod finished the replay and the result is that of a replay without a crash")
        return 0
    except lib.Fail as problem:
        say("\nRESULT: FAIL - " + str(problem))
        return 1
    except KeyboardInterrupt:
        say("\nRESULT: FAIL - you stopped the test with Control-C. The replay may still be running: make results")
        return 1
    except Exception:
        traceback.print_exc()
        say("\nRESULT: FAIL - the script itself broke (see the error above). Paste it to your assistant.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
