# REST evidence: calls every endpoint of the REST API and writes down the request, the answer, the log lines and the numbers that belong to it.
#
#   make rest-evidence                                  about 1 minute; writes evidence/step7-rest-evidence.txt
#   make rest-evidence REST_ARGS="--no-event"           do not send a new tagged event (use the one given with --trace)
#
# The assignment asks for "REST Evidence: endpoint request/response plus relevant logs, metrics, and failure-recovery behavior". This program collects
# the four parts in one file that can be read from top to bottom:
#   1. Requests and answers: each endpoint is called from this computer over HTTPS (with the login where the endpoint needs it, and without it, to show
#      the refusal). One tagged event is sent through the whole pipeline first, so /events/<trace id> has something real to return.
#   2. Logs: the lines the REST API pods wrote for exactly these calls (kubectl logs), as JSON, one line per request.
#   3. Metrics: what Prometheus counted for the endpoints (restapi_requests_total, and the 95th percentile of restapi_request_duration_seconds).
#   4. Failure and recovery: what the last resilience drill (make drill) saw of the REST API when a REST pod and when MongoDB were deleted.
# The login is read from the Secret and never printed. The address of the node is shown as <node-address>. Everything printed is also saved.

import argparse
import json
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

import ca3lib as lib

parser = argparse.ArgumentParser(description="Call every REST endpoint and save request, answer, logs and metrics as evidence.")
parser.add_argument("--no-event", action="store_true", help="do not send a new tagged event (give one with --trace)")
parser.add_argument("--trace", help="the trace id of an event that is in MongoDB already (needs --no-event)")
parser.add_argument("--out", default="evidence", help="folder for the result file (default: evidence)")
parser.add_argument("--prefix", default="step7-rest-evidence", help="name of the result file without .txt (default: step7-rest-evidence)")
parser.add_argument("--drill-prefix", default="step7-drill", help="start of the file names of the drill to quote (default: step7-drill)")
parser.add_argument("--host", help="address of the node to call for the REST API (default: the server, from Terraform)")
parser.add_argument("--port", type=int, help="node port (default: rest_node_port in infra/config.yml)")
args = parser.parse_args()

out_dir = Path(args.out) if Path(args.out).is_absolute() else lib.ROOT / args.out
lines = []
checks = []       # (what, ok)


class Tee:
    def __init__(self, stream):
        self.stream, self.broken = stream, False

    def write(self, text):
        lines.append(text)

        if not self.broken:
            try:
                self.stream.write(text)
                self.stream.flush()
            except Exception:
                self.broken = True

        return len(text)

    def flush(self):
        pass

    def isatty(self):
        return False


def shorten(text, size=700):
    text = " ".join(text.split())
    return text if len(text) <= size else text[:size - 3] + "..."


def judge(what, ok):
    checks.append((what, ok))
    return ok


# One call: prints the request as it went out (the address and the login hidden), the answer, and how long it took. Returns (status, body).
def call(title, path, expect, login=True, **options):
    start = time.monotonic()
    status, body = lib.rest_get(host, port, path, timeout=10, login=login, **options)
    ms = (time.monotonic() - start) * 1000.0
    print("### " + title)
    print("request:   GET https://<node-address>:" + str(port) + path)
    print("           " + ("Authorization: Basic <user and password from the Secret restapi-login>" if login and "password" not in options else
                           ("Authorization: Basic <a user and a WRONG password>" if "password" in options else "(no Authorization header: no login sent)")))
    print("answer:    " + ("HTTP " + str(status) if status is not None else "no answer") + "   (" + format(ms, ".0f") + " ms)   expected: " + str(expect))

    if status == 200 and path.startswith("/alerts"):
        try:
            data = json.loads(body)
            print("           " + str(data.get("count")) + " alerts (flows whose Label is not BENIGN) in the answer; the first one, shortened:")
            print("           " + shorten(json.dumps(lib.trimmed(data["alerts"][0])) if data.get("alerts") else "(none)"))
        except (ValueError, KeyError, TypeError):
            print("           " + shorten(body))
    else:
        print("           " + shorten(body if body.strip() else "(empty body)"))

    print()
    judge(title + ": HTTP " + str(expect), status == expect)
    return status, body


def section(number, title):
    print("=" * 100)
    print(str(number) + ". " + title)
    print("=" * 100)
    print()


started = datetime.now(timezone.utc)
sys.stdout = Tee(sys.stdout)
host = port = None
trace = args.trace
problem_text = None

try:
    print("REST evidence, " + started.strftime("%Y-%m-%d %H:%M UTC") + "   (namespace " + lib.NS + ")")
    print("Every call below was made from this computer over HTTPS, through a node port of the cluster. The node address is shown as <node-address>.\n")
    problems = lib.stack_problems()

    if problems:
        raise lib.Fail("the stack is not ready: " + ", ".join(problems) + ". Run:  make status")

    host, port = lib.rest_target(args.host, args.port)

    if not args.no_event:
        trace = lib.new_trace_id()
        print("Sending one tagged event through the pipeline (producer, Kafka, processor, MongoDB): " + trace)
        sent = lib.send_event(trace)
        print("Kafka stored it at partition " + str(sent["partition"]) + ", offset " + str(sent["offset"]) + ". Waiting for it to reach MongoDB ...")
        found, seconds = lib.wait_for(lambda: lib.rest_get(host, port, "/events/" + trace, timeout=5)[0] == 200, 120, interval=2)
        print(("It is in MongoDB and readable from the REST API " + format(seconds, ".0f") + " s after the producer finished.") if found else "It did not reach MongoDB within 120 seconds (see the 404 below).")
        print()
    elif not trace:
        raise lib.Fail("--no-event needs --trace <id of an event that is in MongoDB>")

    mark = time.time()
    section(1, "Requests and answers")
    call("GET /health  (is the program alive; the liveness probe of Kubernetes; needs no login)", "/health", 200, login=False)
    call("GET /ready  (does MongoDB answer; the readiness probe; needs no login)", "/ready", 200, login=False)
    call("GET /stats  (how many flows are stored; login needed)", "/stats", 200)
    call("GET /alerts  (up to 50 flows that are not BENIGN; login needed)", "/alerts", 200)
    call("GET /events/<trace id>  (the tagged event that was just sent; login needed)", "/events/" + trace, 200)
    call("GET /events/<an id that does not exist>  (the answer is JSON, so a caller can tell 'not here' from 'no such address')", "/events/smoke-does-not-exist", 404)
    call("GET /stats without a login", "/stats", 401, login=False)
    call("GET /stats with a wrong password", "/stats", 401, user="nobody", password="wrong-on-purpose")
    call("GET /no-such-address  (an address that does not exist; with the login)", "/no-such-address", 404)
    status, text = lib.rest_get(host, port, "/stats", timeout=10, ca=False)
    print("### GET /stats when the client does not trust the certificate authority of the stack")
    print("answer:    " + (("HTTP " + str(status)) if status is not None else "no answer") + "   expected: no answer (the TLS handshake must fail)")
    print("           " + shorten(text, 200))
    print()
    judge("TLS: a client that does not trust the stack's CA is refused", status is None)
    time.sleep(2)

    # ---- logs
    section(2, "Logs of the REST API pods for these calls")
    code, out, err = lib.kubectl("-n", lib.NS, "logs", "-l", "app.kubernetes.io/name=restapi", "-c", "restapi", "--since=10m", "--tail=300", "--prefix", timeout=60)
    shown = []

    for line in out.splitlines():
        start_at = line.find("{")

        try:
            record = json.loads(line[start_at:]) if start_at >= 0 else None
        except ValueError:
            record = None

        if record and record.get("msg") == "request" and (record.get("path", "").startswith(("/stats", "/alerts", "/events", "/no-such")) or record.get("status", 0) >= 400):
            shown.append(line.strip())

    print("$ kubectl -n " + lib.NS + " logs -l app.kubernetes.io/name=restapi -c restapi --since=10m --prefix      (the lines about the calls above; /health and /ready are logged only when they fail)")
    print()

    for line in shown[-16:]:
        print("  " + shorten(line, 330))

    if not shown:
        print("  (no request lines found: " + (err.strip() or "the pods wrote none in the last 10 minutes") + ")")

    print()
    judge("the REST API pods logged the calls (JSON lines with method, path, status, duration_ms)", bool(shown))
    print("The event id can be followed through the logs of every program with:  make logs-trace TRACE=" + trace + "\n")

    # ---- metrics
    section(3, "Metrics: what Prometheus counted")

    try:
        rows = lib.prom_query('sum by (endpoint, status) (restapi_requests_total{job="restapi"})')
        print("restapi_requests_total, summed over the REST API pods, by endpoint and status (counted since the pods started):")

        for row in sorted(rows, key=lambda r: (r["metric"].get("endpoint", ""), r["metric"].get("status", ""))):
            if float(row["value"][1]) > 0:
                print("    " + row["metric"].get("endpoint", "?").ljust(24) + row["metric"].get("status", "?").ljust(6) + format(float(row["value"][1]), ",.0f"))

        quantile = lib.prom_number('histogram_quantile(0.95, sum by (le) (rate(restapi_request_duration_seconds_bucket{job="restapi"}[5m])))')
        rate = lib.prom_number('sum(rate(restapi_requests_total{job="restapi"}[5m]))')
        errors = lib.prom_number('sum(rate(restapi_requests_total{job="restapi", status=~"5.."}[5m]))')
        print()
        print("over the last 5 minutes:  requests per second " + ("n/a" if rate is None else format(rate, ".3f")) + ";  server errors (5xx) per second " + ("0" if errors is None else format(errors, ".3f"))
              + ";  95th percentile of the time to answer " + ("n/a" if quantile is None else format(quantile * 1000.0, ".0f") + " ms"))
        judge("Prometheus has the REST API request counters", bool(rows))
    except lib.Fail as problem:
        print("Prometheus did not answer: " + shorten(str(problem), 300))
        print("(Run  make metrics-check  to see which part of the monitoring is not working.)")
        judge("Prometheus has the REST API request counters", False)

    print()

    # ---- failure and recovery
    section(4, "Failure and recovery: what the resilience drill saw")
    summary_file = out_dir / (args.drill_prefix + "-summary.json")

    try:
        drill = json.loads(summary_file.read_text())
        tiers = {t["tier"]: t for t in drill.get("tiers", []) if t.get("deleted")}
        print("From " + args.drill_prefix + "-summary.json (the full table is in " + args.drill_prefix + "-results.md, the second-by-second account in " + args.drill_prefix + "-timeline.txt):\n")

        if "restapi" in tiers:
            r, t = tiers["restapi"]["rest"], tiers["restapi"]
            print("One of the two REST API pods was deleted (kubectl delete pod). The other pod kept answering:")
            print("    " + str(r.get("calls")) + " calls to /stats from this computer during the replacement, " + str(r.get("stats_failed")) + " failed (longest stretch " + format(r.get("stats_gap_s", 0), ".1f") + " s);"
                  " the new pod was ready " + format(t.get("pod_back_s") or 0, ".0f") + " s after the delete.")
        else:
            print("The REST API tier is not in the saved drill. Run:  make drill DRILL_ARGS=\"--only restapi\"  (or the whole drill:  make drill).")

        if "mongo" in tiers:
            r, t = tiers["mongo"]["rest"], tiers["mongo"]
            print("MongoDB was deleted. The design says that the REST API pods report 'not ready' while MongoDB is away (the readiness probe asks /ready), so that Kubernetes takes them out of the Service. What the drill saw:")
            print("    " + str(r.get("calls")) + " calls from this computer during that tier: /stats failed " + str(r.get("stats_failed")) + " times (longest stretch " + format(r.get("stats_gap_s", 0), ".1f") + " s), /health failed "
                  + str(r.get("health_failed")) + " times (longest stretch " + format(r.get("health_gap_s", 0), ".1f") + " s);"
                  " MongoDB was ready again " + format(t.get("pod_back_s") or 0, ".0f") + " s after the delete, the whole stack " + format(t.get("whole_s") or 0, ".0f") + " s.")
            print("    While MongoDB is gone the API cannot return data, whichever way it is built.")
        else:
            print("The MongoDB tier is not in the saved drill.")
    except (OSError, ValueError, KeyError):
        print("There is no saved drill (" + args.drill_prefix + "-summary.json). Run  make drill  first; this part of the evidence quotes it.")

    print()
except lib.Fail as problem:
    problem_text = " ".join(str(problem).split())
except KeyboardInterrupt:
    problem_text = "stopped by the user (Ctrl-C)"
except Exception:
    traceback.print_exc()
    problem_text = "the program itself broke (see the error above)"

sys.stdout = sys.stdout.stream if isinstance(sys.stdout, Tee) else sys.stdout
failed = [what for what, ok in checks if not ok]

try:
    out_dir.mkdir(parents=True, exist_ok=True)
    # The node address is never printed here (it is shown as <node-address>), but an error message could carry one: "make scrub" removes it before the commit.
    (out_dir / (args.prefix + ".txt")).write_text("".join(lines))
    print("Saved: " + args.out + "/" + args.prefix + ".txt")
except OSError as problem:
    print("The file could not be saved: " + str(problem))

if problem_text:
    print("\nRESULT: FAIL - " + problem_text)
    sys.exit(1)

if failed:
    print("\nRESULT: FAIL - " + str(len(failed)) + " of " + str(len(checks)) + " checks did not match: " + "; ".join(failed[:4]))
    sys.exit(1)

print("\nRESULT: PASS - " + str(len(checks)) + " of " + str(len(checks)) + " checks matched (every endpoint answered as expected, the calls are in the logs and in the metrics)")
