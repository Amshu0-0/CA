# Prints what the detectors said about a replay, from the REST API of the cluster (HTTPS, with the login).
#
#   make results                                    the newest replay, as it is now
#   make replay                                     (calls this with --replay-job --wait: waits for the processor to finish THIS replay)
#   make results RESULTS_ARGS="--run run-20261114-091500 --windows 10 --compare evidence/local-replay.json"
#
# What it does:
#   1. Finds the replay: --run NAME, or with --replay-job the name in the log of the Job producer-replay, or else the newest one.
#   2. With --wait SECONDS it waits until the processor has scored the last window (the run's status is "complete"), and says how far it is.
#   3. Prints the table of GET /summary: for each detector the windows it got right and wrong against the dataset's alert tags.
#   4. With --windows N it also prints the first N windows that hold an alert line, with what each detector said about them.
#   5. With --compare FILE it compares the numbers with the summary that  make local-replay --save  wrote (the same detectors, on your computer),
#      and it always compares the number of windows and alert lines with bundle/replay-info.json (what the NLP code expects from the file).
# Nothing in the cluster is changed. The login is read from the Secret restapi-login with kubectl; the password is never printed.

import argparse
import json
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

import nlplib as lib
import tables

DEFAULT_JOB = "producer-replay"


# The name of the replay that the Job's producer started: the "producer started" line of its log. The last one counts (a producer that
# was restarted by Kubernetes starts a new replay under a new name).
def run_of_job(job=DEFAULT_JOB):
    code, out, err = lib.kubectl("-n", lib.NS, "logs", "job/" + job, "-c", "producer", timeout=60)

    if code != 0:
        raise lib.Fail("could not read the log of the Job " + job + ": " + (err.strip() or "no answer") + ". Was it started?  make replay")

    found = None

    for line in out.splitlines():
        start = line.find("{")

        if start < 0:
            continue

        try:
            record = json.loads(line[start:])
        except ValueError:
            continue

        if isinstance(record, dict) and record.get("msg") == "producer started" and record.get("run"):
            found = record["run"]

    if not found:
        raise lib.Fail("the log of the Job " + job + " has no 'producer started' line yet. Its log:\n" + (out.strip()[-600:] or "(empty)"))

    return found


def get(path, wanted=(200,)):
    status, body = lib.rest_json(path)

    if status not in wanted:
        raise lib.Fail("GET " + path + " answered HTTP " + str(status) + ": " + json.dumps(body)[:300])

    return body


# The row of /runs for this replay, or None while the processor has not stored a line of it.
def find_run(name):
    for run in get("/runs?limit=100")["runs"]:
        if run["run"] == name:
            return run

    return None


# Wait until the replay is finished (complete, or superseded by a newer one). Prints a line about every 10 seconds.
def wait_for_run(name, seconds, say=print):
    deadline = time.monotonic() + seconds
    last_print, last_change, last_seen = None, time.monotonic(), None

    while True:
        run = find_run(name)
        state = None if run is None else (run["status"], run["lines_stored"], run["windows_stored"])

        if run is not None and run["status"] in ("complete", "superseded"):
            return run

        if state != last_seen:
            last_seen, last_change = state, time.monotonic()

        if last_print is None or time.monotonic() - last_print >= 10:
            say("  " + ("the processor has not stored anything of " + name + " yet" if run is None else
                        run["status"] + ": " + str(run["lines_stored"]) + " lines and " + str(run["windows_stored"]) + " windows stored so far"))
            last_print = time.monotonic()

        if time.monotonic() - last_change > 240:
            raise lib.Fail("nothing new was stored for 4 minutes. Is the processor alive?  make logs   (and  make status)")

        if time.monotonic() >= deadline:
            raise lib.Fail("the replay " + name + " was not finished after " + str(seconds) + " seconds. It may still be working: run  make results  later")

        time.sleep(3)


def clock(epoch):
    return datetime.fromtimestamp(int(epoch), tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


# The first windows that hold an alert line, with the verdict of each detector.
def show_windows(name, count):
    body = get("/windows?run=" + name + "&flagged=truth&limit=" + str(count))
    print("\nThe first " + str(len(body["windows"])) + " windows that hold an alert line (the dataset says: anomalous). 1 = the detector flagged the window:")
    print("  " + "start (UTC)".ljust(18) + "lines".rjust(7) + "alert lines".rjust(13) + "LogBERT".rjust(9) + "LLM".rjust(6) + "keywords".rjust(10) + "  missed keys")

    for w in body["windows"]:
        print("  " + clock(w["start"]).ljust(18) + str(w["n_messages"]).rjust(7) + str(w["truth_alerts"]).rjust(13) + str(w["logbert"]).rjust(9) + str(w["llm"]).rjust(6)
              + str(w["keyword"]).rjust(10) + "  " + str(w["logbert_missed"]))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Print what the detectors said about a replay.")
    parser.add_argument("--run", help="the name of the replay (default: the newest)")
    parser.add_argument("--replay-job", action="store_true", help="the replay that the Job producer-replay started (make replay uses this)")
    parser.add_argument("--wait", type=int, default=0, metavar="SECONDS", help="wait up to this long for the processor to finish the replay")
    parser.add_argument("--windows", type=int, default=0, metavar="N", help="also show the first N windows that hold an alert line")
    parser.add_argument("--compare", metavar="FILE", help="compare the numbers with a summary saved by  make local-replay --save")
    parser.add_argument("--json", metavar="FILE", help="save the summary of the cluster in this file")
    args = parser.parse_args(argv)

    try:
        name = args.run or (run_of_job() if args.replay_job else None)

        if name is None:
            runs = get("/runs?limit=1")["runs"]

            if not runs:
                raise lib.Fail("no replay has been stored yet. Start one with:  make replay")

            name = runs[0]["run"]

        if args.wait:
            print("Waiting for the processor to finish the replay " + name + " (up to " + str(args.wait) + " s)...")
            wait_for_run(name, args.wait)

        summary = get("/summary?run=" + name)
        print()

        for text in tables.describe(summary):
            print(text)

        if summary["run"].get("status") != "complete":
            print("\n(The replay is not complete: " + str(summary["run"].get("status")) + ". The numbers are those of the windows scored so far.)")

        if args.windows:
            show_windows(name, args.windows)

        for text in tables.reference(Path(__file__).resolve().parent.parent.parent / "NLP" / "results" / "comparison.csv"):
            print(("\n" if text.startswith("For comparison") else "") + text)

        problems = []
        info_file = Path(__file__).resolve().parent.parent / "bundle" / "replay-info.json"

        if info_file.is_file() and summary["run"].get("status") == "complete":
            info = json.loads(info_file.read_text(encoding="utf-8"))
            got = {"lines": summary["lines"]["stored"], "alert_lines": summary["lines"]["with_alert_tag"], "windows": summary["windows"], "anomalous_windows": summary["anomalous_windows"]}
            wrong = [key + ": the NLP code expects " + str(info[key]) + ", the cluster has " + str(value) for key, value in got.items() if info[key] != value]

            if wrong:
                problems += ["the cluster's windows are not the ones the NLP code builds from the file (bundle/replay-info.json):"] + ["    " + w for w in wrong]
            else:
                print("\nThe cluster built exactly the windows that the NLP code expects from the replay file: " + str(info["windows"]) + " windows, " + str(info["anomalous_windows"])
                      + " anomalous, " + str(info["alert_lines"]) + " alert lines.")

        if args.compare:
            local = json.loads(Path(args.compare).read_text(encoding="utf-8"))
            different = tables.differences(local, summary, ("laptop", "cluster"))

            if not different:
                print("\nThe cluster and your computer (" + args.compare + ") give the same numbers for every detector.")
            else:
                print("\nThe cluster and your computer (" + args.compare + ") differ in:")

                for line in different:
                    print("  " + line)

                print("  Keyword rules and saved LLM decisions must be identical. LogBERT can differ by a window or two between a Mac (ARM) and the cluster (x86): "
                      "floating-point rounding moves a masked key across the top-g line now and then.")
                problems += [line for line in different if "LogBERT" not in line]

        if args.json:
            Path(args.json).parent.mkdir(parents=True, exist_ok=True)
            Path(args.json).write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
            print("\nThe summary is saved in " + args.json)

        if problems:
            print("\nRESULT: FAIL")

            for p in problems:
                print("  - " + p)

            return 1

        print("\nRESULT: PASS - the replay " + name + " is " + str(summary["run"].get("status")))
        return 0
    except lib.Fail as problem:
        print("\nRESULT: FAIL - " + str(problem))
        return 1
    except Exception:
        traceback.print_exc()
        print("\nRESULT: FAIL - the script itself broke (see the error above). Paste it to your assistant.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
