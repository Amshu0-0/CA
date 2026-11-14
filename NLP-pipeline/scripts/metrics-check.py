# Checks the monitoring: are the numbers and the logs of the pipeline really arriving, and can Grafana show them?
#
#   make metrics-check                        (read-only: nothing in the cluster is changed; best run after  make replay )
#   make logs-trace TRACE=smoke-1a2b3c4d      (follow one log line through the logs of every program)
#
#   1. Prometheus is ready and every program it should read is "up": processor, REST API, kafka-exporter, mongodb-exporter. The producer
#      is read too, but it exists only while a replay (or a smoke test) runs, so it is not demanded when none is running.
#   2. The numbers exist as series: producer rate, consumer lag of the group processor, database inserts per second (MongoDB's own count),
#      log lines and windows handled by the processor, windows flagged by each detector, LogBERT seconds per window, REST requests, errors
#      and latency. Their current values are printed. A number that only a replay makes is reported as "not yet" if no replay has run.
#   3. Loki is ready and holds logs from every program (Alloy reads them from the pods and sends them there).
#   4. Grafana is healthy, the dashboard "NLP log pipeline" is loaded, and both data sources (Prometheus and Loki) answer.
#
# The questions are asked from inside the pods of Prometheus, Loki and Grafana ("kubectl exec ... wget http://localhost:..."), because nothing
# outside the cluster can reach them. The Grafana login is read from the mounted Secret by the shell inside the pod: it is never printed.
#
# With --trace the script instead asks Loki for every log line that contains this trace id, and shows them oldest first: the journey of one
# log line, from the producer to the processor (and the REST API, if it was asked for the line). The smoke test prints such an id.

import argparse
import json
import re
import sys
import time
import traceback
from datetime import datetime, timezone

import nlplib as lib

JOBS = ("processor", "restapi", "kafka-exporter", "mongodb-exporter", "prometheus")

# The producer exists only while a replay or a smoke test runs (a Job), so Prometheus has a target for it only then.
SOMETIMES = ("producer",)
LOG_APPS = ("processor", "restapi", "kafka", "mongo", "prometheus", "grafana")

# The logs of the producer are in Loki once a smoke test or a replay has run.
LOG_APPS_SOMETIMES = ("producer",)

# (what it is, the number to show, a selector that must match at least one series, True if only a replay makes the series)
# "In the last 24 hours" is how long Prometheus keeps the numbers; a series of a pod that is gone is still found with last_over_time.
NUMBERS = (
    ("processor ready (models loaded, Kafka and MongoDB connected)", "min(processor_ready)", "processor_ready", False),
    ("messages the producer handed to Kafka (last 24 hours)", "sum(max_over_time(producer_messages_sent_total[24h]))", "producer_messages_sent_total", True),
    ("consumer lag of the group processor, messages", 'sum(kafka_consumergroup_lag{group="processor"})', 'kafka_consumergroup_lag{group="processor"}', True),
    ("database writes per second (MongoDB's own count of inserts and updates)", 'sum(rate(mongodb_ss_opcounters{legacy_op_type=~"insert|update"}[1m]))', 'mongodb_ss_opcounters{legacy_op_type=~"insert|update"}', False),
    ("log lines stored by the processor since it started", "sum(processor_messages_total)", "processor_messages_total", False),
    ("windows scored by the processor since it started", "sum(processor_windows_total)", "processor_windows_total", False),
    ("windows flagged by LogBERT since it started", 'sum(processor_windows_flagged_total{detector="logbert"})', 'processor_windows_flagged_total{detector="logbert"}', True),
    ("windows flagged by the rule-guided LLM since it started", 'sum(processor_windows_flagged_total{detector="llm"})', 'processor_windows_flagged_total{detector="llm"}', True),
    ("windows flagged by the keyword rules since it started", 'sum(processor_windows_flagged_total{detector="keyword"})', 'processor_windows_flagged_total{detector="keyword"}', True),
    ("LogBERT seconds per window, mean", "sum(processor_window_score_seconds_sum) / sum(processor_window_score_seconds_count)", "processor_window_score_seconds_count", False),
    ("REST requests per second", "sum(rate(restapi_requests_total[1m]))", "restapi_requests_total", False),
    ("REST server errors per second (status 5xx)", 'sum(rate(restapi_requests_total{status=~"5.."}[1m]))', "restapi_requests_total", False),
    ("REST latency, 95% of the answers faster than (seconds)", "histogram_quantile(0.95, sum by (le) (rate(restapi_request_duration_seconds_bucket[5m])))", "restapi_request_duration_seconds_bucket", False),
)


def say(text):
    print("      " + text)


def show(value):
    if value is None:
        return "no value (nothing to measure yet)"

    return str(int(value)) if value == int(value) and abs(value) < 10**12 else str(round(value, 4))


def check_prometheus():
    ready = lib.local_get("deployment/prometheus", 9090, "/-/ready").strip()
    say("Prometheus says: " + ready)

    if not ready.endswith("is Ready."):
        raise lib.Fail("Prometheus is not ready yet (it says: " + ready + "). Wait a minute and run the check again")

    targets = json.loads(lib.local_get("deployment/prometheus", 9090, "/api/v1/targets?state=active"))["data"]["activeTargets"]
    by_job, problems = {}, []

    for t in targets:
        by_job.setdefault(t["labels"]["job"], []).append(t)

    for job in JOBS:
        found = by_job.get(job, [])
        up = [t for t in found if t["health"] == "up"]
        say(job.ljust(18) + str(len(up)) + " of " + str(len(found)) + " targets up")

        if not found:
            problems.append("Prometheus has no target for the job " + job)

        problems += [job + ": " + t["scrapeUrl"] + " is " + t["health"] + " (" + (t.get("lastError") or "no error text yet") + ")" for t in found if t["health"] != "up"]

    for job in SOMETIMES:
        found = by_job.get(job, [])
        up = [t for t in found if t["health"] == "up"]

        if found:
            say(job.ljust(18) + str(len(up)) + " of " + str(len(found)) + " targets up (a replay or a smoke test is running)")
            problems += [job + ": " + t["scrapeUrl"] + " is " + t["health"] + " (" + (t.get("lastError") or "no error text yet") + ")" for t in found if t["health"] != "up"]
        else:
            say(job.ljust(18) + "no pod right now (it exists only while a replay or a smoke test runs)")

    if problems:
        raise lib.Fail("\n".join(problems) + "\nLook at the pod:  make status   and  make diagnose")

    return "every one of the " + str(len(targets)) + " targets is up"


def check_numbers():
    missing, waiting = [], []

    for what, expression, selector, needs_replay in NUMBERS:
        if not lib.prom_query("count(last_over_time(" + selector + "[24h]))"):
            if needs_replay:
                say("not yet   " + what + "   (appears after a replay: make replay)")
                waiting.append(selector)
            else:
                say("MISSING   " + what + "   (no series " + selector + ")")
                missing.append(selector)

            continue

        say(what.ljust(62) + show(lib.prom_number(expression)))

    if missing:
        raise lib.Fail("these series do not exist in Prometheus: " + ", ".join(sorted(set(missing))) + ". The program that publishes them is not being read (see the check above), or has not run yet")

    if waiting:
        return str(len(NUMBERS) - len(waiting)) + " numbers exist; " + str(len(waiting)) + " more appear after the first replay (run make replay, then this check again)"

    return "all " + str(len(NUMBERS)) + " numbers exist"


def check_loki():
    ready = lib.local_get("deployment/loki", 3100, "/ready").strip()
    say("Loki says: " + ready)

    if ready != "ready":
        raise lib.Fail("Loki is not ready yet (it says: " + ready + "). It needs about 15 seconds after it starts; run the check again")

    apps = json.loads(lib.local_get("deployment/loki", 3100, "/loki/api/v1/label/app/values")).get("data", [])
    say("programs with logs in Loki: " + (", ".join(sorted(apps)) or "none"))
    levels = json.loads(lib.local_get("deployment/loki", 3100, "/loki/api/v1/label/level/values")).get("data", [])
    say("levels seen in the logs:    " + (", ".join(sorted(levels)) or "none"))
    missing = [a for a in LOG_APPS if a not in apps]

    if missing:
        raise lib.Fail("Loki has no logs from: " + ", ".join(missing) + ". Alloy reads them from the pods and sends them to Loki. Look at:  kubectl -n " + lib.NS + " logs daemonset/alloy")

    for app in LOG_APPS_SOMETIMES:
        if app not in apps:
            say("no logs from the " + app + " yet (it exists only for a smoke test or a replay: run  make smoke  or  make replay )")

    if not levels:
        raise lib.Fail("Loki has logs, but none with a level label: the json stage of Alloy did not read the JSON lines of the programs")

    return "logs of " + str(len([a for a in LOG_APPS + LOG_APPS_SOMETIMES if a in apps])) + " programs are in Loki, with level labels"


def check_grafana():
    health = json.loads(lib.local_get("deployment/grafana", 3000, "/api/health"))
    say("health: database " + str(health.get("database")) + ", version " + str(health.get("version")))

    if health.get("database") != "ok":
        raise lib.Fail("Grafana answers, but its database is not ok: " + json.dumps(health))

    titles = [d.get("title") for d in json.loads(lib.grafana_get("/api/search?query=NLP"))]
    say("dashboards found: " + (", ".join(titles) or "none"))

    if "NLP log pipeline" not in titles:
        raise lib.Fail("the dashboard NLP log pipeline is not loaded. Look at:  kubectl -n " + lib.NS + " logs deployment/grafana   (provisioning errors are logged at the start)")

    for uid in ("prometheus", "loki"):
        answer = json.loads(lib.grafana_get("/api/datasources/uid/" + uid + "/health"))
        say("data source " + uid.ljust(11) + str(answer.get("status")) + ": " + str(answer.get("message")))

        if answer.get("status") != "OK":
            raise lib.Fail("the data source " + uid + " does not answer: " + json.dumps(answer)[:300])

    return "the dashboard is loaded and both data sources answer"


CHECKS = (
    ("prometheus", "Prometheus reads every program", check_prometheus),
    ("numbers", "The numbers of the pipeline exist", check_numbers),
    ("loki", "Loki holds the logs of every program", check_loki),
    ("grafana", "Grafana has the dashboard and both data sources", check_grafana),
)


def run_checks():
    results = []

    for number, (key, title, run) in enumerate(CHECKS, 1):
        print("\n[" + str(number) + "/" + str(len(CHECKS)) + "] " + title)

        try:
            results.append((key, True, run()))
        except lib.Fail as problem:
            parts = str(problem).splitlines() or [str(problem)]
            say("FAIL: " + parts[0])

            for extra in parts[1:]:
                say("      " + extra)

            results.append((key, False, parts[0]))
        except Exception:
            traceback.print_exc()
            results.append((key, False, "the check itself broke (see the error above)"))

    print("\nSummary")

    for key, passed, line in results:
        print("  " + ("PASS" if passed else "FAIL") + "  " + key.ljust(11) + line)

    if all(passed for _, passed, _ in results):
        print("\nRESULT: PASS - " + str(len(results)) + " of " + str(len(results)) + " monitoring checks passed")
        return 0

    print("\nRESULT: FAIL - " + str(sum(1 for r in results if not r[1])) + " of " + str(len(results)) + " monitoring checks failed")
    return 1


def follow_trace(trace, wait):
    print("Following the log line " + trace + " through the logs in Loki (namespace " + lib.NS + ")\n")

    def found():
        lines = lib.loki_lines('{namespace="' + lib.NS + '"} |= "' + trace + '"')
        apps = {labels.get("app") for _, labels, _ in lines}
        return lines if {"producer", "processor"} <= apps else None

    lines, _ = lib.wait_for(found, wait, interval=5)

    if not lines:
        lines = lib.loki_lines('{namespace="' + lib.NS + '"} |= "' + trace + '"')

    for ts, labels, line in lines:
        when = datetime.fromtimestamp(ts / 1e9, timezone.utc).strftime("%H:%M:%S.%f")[:-3]
        print("  " + when + "  " + str(labels.get("app", "?")).ljust(11) + str(labels.get("level", "-")).ljust(8) + line[:230])

    apps = {labels.get("app") for _, labels, _ in lines}
    print()

    if {"producer", "processor"} <= apps:
        print("RESULT: PASS - " + str(len(lines)) + " log lines with this trace id, from " + ", ".join(sorted(apps)) + ": one id, one story across programs")
        return 0

    print("RESULT: FAIL - " + (str(len(lines)) + " log line(s), from " + ", ".join(sorted(a for a in apps if a)) if lines else "no log line") + " with this trace id; the producer and the processor must both be there.")
    print("        Alloy needs a few seconds to forward a line. Wait half a minute and run the same command again. If it still fails: make metrics-check")
    return 1


parser = argparse.ArgumentParser(description="Check the monitoring (Prometheus, Loki, Grafana), or follow one trace id through the logs.")
parser.add_argument("--trace", metavar="ID", help="follow this trace id through the logs (the smoke test prints one)")
parser.add_argument("--wait", type=int, default=60, help="with --trace: seconds to wait for the lines to arrive (default 60)")
args = parser.parse_args()

try:
    if args.trace is not None:
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{6,80}", args.trace):
            sys.exit("Give the trace id after TRACE=, for example:  make logs-trace TRACE=smoke-1a2b3c4d   (the smoke test prints it: 'trace id: ...')")

        sys.exit(follow_trace(args.trace, args.wait))

    print("Monitoring check (namespace " + lib.NS + "): asks Prometheus, Loki and Grafana from inside their pods")
    sys.exit(run_checks())
except lib.Fail as problem:
    print("\nRESULT: FAIL - " + str(problem))
    sys.exit(1)
except Exception:
    traceback.print_exc()
    print("\nRESULT: FAIL - the check itself broke (see the error above). Paste it to your assistant.")
    sys.exit(1)
