# Operator steps: the manual things from the runbook (runbook.md), done once on the running stack, with what they print saved as evidence.
#
#   make operator-steps                          about 2 minutes; writes evidence/step7-operator-steps.txt
#   make operator-steps OPERATOR_ARGS="--skip-scale"      only look (logs and certificates), change nothing
#
# The assignment asks to "document manual steps, for example check logs, scale replicas, rotate a certificate". runbook.md explains when and why.
# This program does the parts that are safe on a running stack, so the commands in the runbook are shown with their real output:
#   1. Check the logs: the last lines of every program (kubectl logs), and the error search in Loki. Loki also keeps the lines of pods that are gone.
#   2. Scale replicas: the REST API goes from 2 pods to 3 (kubectl scale), the third pod becomes ready, the API still answers, and then it goes back to 2.
#      It is the REST API because nothing else changes its number of pods: the autoscalers own the processor and the load generator.
#      If the program stops in the middle (Ctrl-C, closed window), the REST API is put back to its normal number before the program ends.
#   3. Check the certificates: the end date of every certificate in the Secrets. Only the certificates are read; no key is printed or saved.
#   4. Rotate a certificate: NOT done here, on purpose. The program says why and what it takes (see also the end of processor/bootstrap.py).
# Nothing here deletes data.

import argparse
import re
import shutil
import signal
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

import ca3lib as lib

parser = argparse.ArgumentParser(description="Do the manual steps of the runbook once and save the output as evidence.")
parser.add_argument("--skip-scale", action="store_true", help="do not scale the REST API (only read logs and certificates)")
parser.add_argument("--wait", type=int, default=240, help="seconds to wait for each scaling step (default 240)")
parser.add_argument("--out", default="evidence", help="folder for the result file (default: evidence)")
parser.add_argument("--prefix", default="step7-operator-steps", help="name of the result file without .txt (default: step7-operator-steps)")
parser.add_argument("--host", help="address of the node to call for the REST API (default: the server, from Terraform)")
parser.add_argument("--port", type=int, help="node port (default: rest_node_port in infra/config.yml)")
args = parser.parse_args()

out_dir = Path(args.out) if Path(args.out).is_absolute() else lib.ROOT / args.out
lines = []
checks = []       # (what, ok)
LOKI_ERRORS = '{namespace="' + lib.NS + '"} |~ "(?i)(error|exception|traceback|refused|timed out)" != "Checking authorization failed"'

# The programs: what to call them, the label app.kubernetes.io/name, the container whose log is read, and a second label (for the load generator).
PROGRAMS = (("Kafka", "kafka", "kafka", None), ("Processor", "processor", "processor", None), ("MongoDB", "mongo", "mongo", None),
            ("REST API", "restapi", "restapi", None), ("Producer (load generator)", "producer", "producer", "load-generator"))

# Every certificate that the bootstrap Job puts in a Secret: the Secret, the key that holds the certificate, who uses it.
CERTIFICATES = (("pki-ca", "ca.crt", "the private certificate authority (CA)"),
                ("kafka-tls", "keystore.pem", "Kafka broker (server)"),
                ("mongo-tls", "mongo.pem", "MongoDB (server)"),
                ("restapi-tls", "tls.crt", "REST API (server)"),
                ("kafka-client-producer", "tls.crt", "producer (client of Kafka)"),
                ("kafka-client-processor", "tls.crt", "processor (client of Kafka)"),
                ("kafka-client-exporter", "tls.crt", "Kafka metrics reader (client of Kafka)"))
DAYS_WARNING = 30

stopped_by = []
changed = {"replicas": None}      # the normal number of REST API pods, while the REST API is scaled away from it


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


def ignore_signals():
    for name in ("SIGINT", "SIGHUP", "SIGTERM"):
        try:
            signal.signal(getattr(signal, name), signal.SIG_IGN)
        except (OSError, ValueError, AttributeError):
            pass


def stop_on_signal(number, frame):
    ignore_signals()      # a second Ctrl-C must not break the clean-up
    stopped_by.append(signal.Signals(number).name)
    raise KeyboardInterrupt


def shorten(text, size=240):
    text = " ".join(str(text).split())
    return text if len(text) <= size else text[:size - 3] + "..."


def judge(what, ok):
    checks.append((what, ok))
    print(("   ok:       " if ok else "   PROBLEM:  ") + what)
    return ok


def section(number, title):
    print("=" * 100)
    print(str(number) + ". " + title)
    print("=" * 100)
    print()


def command(text):
    print("   $ " + text)


# ---------------------------------------------------------------- 1. the logs

def first_pod(name, component):
    pods = [p for p in lib.pipeline_pods(name)
            if not any(o.get("kind") == "Job" for o in p["metadata"].get("ownerReferences", []))
            and (component is None or p["metadata"].get("labels", {}).get("app.kubernetes.io/component") == component)]
    return sorted(pods, key=lambda p: p["metadata"]["name"])[0]["metadata"]["name"] if pods else None


def step_logs():
    section(1, "Check the logs")
    print("First question when something is wrong: what did the program say? The producer, the processor and the REST API write one JSON line per event (Kafka and MongoDB write their own format), so the last lines tell what the program did last.\n")

    for title, name, container, component in PROGRAMS:
        pod = first_pod(name, component)

        if pod is None:
            command("kubectl -n " + lib.NS + " get pods -l app.kubernetes.io/name=" + name)
            judge(title + ": a pod exists", False)
            continue

        command("kubectl -n " + lib.NS + " logs " + pod + " -c " + container + " --tail=3")
        code, out, err = lib.kubectl("-n", lib.NS, "logs", pod, "-c", container, "--tail=3", timeout=40)

        for line in out.splitlines()[-3:]:
            print("       " + shorten(line, 230))

        if code != 0:
            print("       (kubectl said: " + shorten(err, 200) + ")")

        judge(title + ": the log of " + pod + " can be read", code == 0 and bool(out.strip()))
        print()

    print("A pod that crashed and was restarted keeps the log of its last run:  kubectl -n " + lib.NS + " logs <pod> --previous")
    print("A pod that was deleted takes its log with it, but Loki has kept the lines for 24 hours (Grafana > Explore > Loki, or the search below).\n")

    command("Loki:  " + LOKI_ERRORS + "      (the last 15 minutes)")

    try:
        found = lib.loki_lines(LOKI_ERRORS, minutes=15, limit=500)
        pods = sorted({labels.get("pod", "?") for _, labels, _ in found})
        print("       " + str(len(found)) + " line" + ("" if len(found) == 1 else "s") + " in " + str(len(pods)) + " pod" + ("" if len(pods) == 1 else "s")
              + (": " + ", ".join(pods[:6]) + (" ..." if len(pods) > 6 else "") if pods else "") + ".")

        for _, labels, line in found[-3:]:
            print("       " + labels.get("pod", "?") + ": " + shorten(line, 200))

        print("       (The search leaves out the lines of mongo-0 that say 'Checking authorization failed': the small account of the metrics reader is refused all the time, and in")
        print("       those lines the word error is only the name of a field. After a drill there are lines on purpose: the pods that lost MongoDB or Kafka said so.)")
        judge("Loki answers the error search", True)
    except (lib.Fail, ValueError, KeyError) as problem:
        print("       Loki did not answer: " + shorten(problem, 300))
        judge("Loki answers the error search", False)

    print()


# ---------------------------------------------------------------- 2. the replicas

def rest_counts():
    desired = lib.kube_json("-n", lib.NS, "get", "deployment", "restapi")["spec"].get("replicas")
    pods = lib.pipeline_pods("restapi")
    living = [p for p in pods if not p["metadata"].get("deletionTimestamp")]
    return desired, len(pods), len(living), len([p for p in living if lib.pod_is_ready(p)])


def scale_rest(number):
    command("kubectl -n " + lib.NS + " scale deployment/restapi --replicas=" + str(number))
    code, out, err = lib.kubectl("-n", lib.NS, "scale", "deployment/restapi", "--replicas=" + str(number), timeout=40)
    print("       " + shorten(out.strip() or err.strip() or "(no output)", 200))
    return code == 0


def step_scale():
    section(2, "Scale replicas")
    print("A replica is a means, not a goal. The REST API has 2 pods so that it keeps answering while one of them is replaced (the goal 'api-gap' in slo.json).")
    print("To see the manual step, the REST API goes to 3 pods and then back. This is the only Deployment without an autoscaler.\n")
    desired, total, living, ready = rest_counts()
    command("kubectl -n " + lib.NS + " get deployment restapi")
    print("       now: " + str(desired) + " wanted, " + str(ready) + " ready (" + str(living) + " pods)")

    if not judge("the REST API starts with all its pods ready (" + str(ready) + " of " + str(desired) + ")", bool(desired) and ready == desired == living):
        print("       The stack is not at rest, so scaling would prove nothing. Run  make status  and try again.\n")
        return

    normal = desired
    wanted = normal + 1
    changed["replicas"] = normal
    begin = time.monotonic()

    if not judge("kubectl scale to " + str(wanted) + " was accepted", scale_rest(wanted)):
        changed["replicas"] = None
        return

    def reached():
        d, t, l, r = rest_counts()
        return d == wanted and r == wanted and l == wanted

    done, seconds = lib.wait_for(reached, args.wait, interval=3)
    d, t, l, r = rest_counts()
    print("       after " + format(time.monotonic() - begin, ".0f") + " s: " + str(d) + " wanted, " + str(r) + " ready (" + str(l) + " pods)")
    judge("the extra pod became ready within " + str(args.wait) + " s (" + str(wanted) + " of " + str(wanted) + " ready)", bool(done))
    print()

    host, port = lib.rest_target(args.host, args.port)
    command("curl https://<node-address>:" + str(port) + "/stats      (with the login of the Secret restapi-login)")
    status, body = lib.rest_get(host, port, "/stats", timeout=10)
    print("       HTTP " + str(status) + "   " + shorten(body, 200))
    judge("the REST API answers /stats with " + str(wanted) + " pods", status == 200)
    print()

    print("Back to the normal number. The file restapi/restapi.yaml says " + str(normal) + ". A change made with  kubectl scale  is not in the file, and  kubectl apply -k .  does not undo it")
    print("(apply only sends what changed in the file), so it is undone by hand here. To keep a different number, change  replicas:  in the file.")
    begin = time.monotonic()
    ok = scale_rest(normal)

    if ok:
        changed["replicas"] = None

    def back():
        d, t, l, r = rest_counts()
        return d == normal and t == normal and r == normal

    done, seconds = lib.wait_for(back, args.wait, interval=3)
    d, t, l, r = rest_counts()
    print("       after " + format(time.monotonic() - begin, ".0f") + " s: " + str(d) + " wanted, " + str(r) + " ready (" + str(t) + " pods listed)")
    judge("the REST API is back to " + str(normal) + " pods, all ready", ok and bool(done))
    print()
    print("The other two parts that have more than one pod are not scaled by hand: the processor (1 to 3 pods, processor/hpa.yaml) and the load generator (1 to 5, producer/load/hpa.yaml)")
    print("belong to their autoscalers. A  kubectl scale  on them is undone within about 15 seconds. To change how many they may have, change minReplicas / maxReplicas in the file")
    print("and run  kubectl apply -k .  The processor cannot use more than 3 pods: the Kafka topic has 3 partitions, and one partition is read by one processor at a time.\n")


# ---------------------------------------------------------------- 3. the certificates

def certificate_facts(secret, key):
    """Reads one Secret, keeps only the CERTIFICATE part (never the key), and asks openssl for the name and the end date. Returns (name, end date) or raises lib.Fail."""
    text = lib.secret_value(secret, key).decode("utf-8", "replace")
    blocks = re.findall(r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", text, flags=re.S)
    text = None       # the rest (a private key in the same file) is dropped here

    if not blocks:
        raise lib.Fail("no certificate in " + secret + "/" + key)

    done = subprocess.run(["openssl", "x509", "-noout", "-subject", "-enddate"], input=blocks[0] + "\n", capture_output=True, text=True, timeout=30)

    if done.returncode != 0:
        raise lib.Fail("openssl could not read the certificate of " + secret + ": " + shorten(done.stderr, 150))

    name = re.search(r"CN\s*=\s*([^,/\n]+)", done.stdout)
    end = re.search(r"notAfter=(.+)", done.stdout)

    if not name or not end:
        raise lib.Fail("openssl printed no name or end date for " + secret)

    when = datetime.strptime(" ".join(end.group(1).split()), "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
    return name.group(1).strip(), when


def step_certificates():
    section(3, "Check the certificates")
    print("A certificate is valid until a fixed date. The bootstrap Job makes them valid for 365 days (VALID_DAYS in processor/bootstrap.py).")
    print("The end dates are read from the Secrets; only the certificate part is looked at, no key is printed or saved.\n")

    if shutil.which("openssl") is None:
        print("   openssl was not found on this computer, so the dates cannot be read here. (On the cluster nodes:  openssl x509 -noout -enddate)\n")
        judge("openssl is installed on this computer", False)
        return

    command("kubectl -n " + lib.NS + " get secret <name> -o jsonpath='{.data.<key>}' | base64 --decode | openssl x509 -noout -subject -enddate")
    print()
    print("   " + "Secret".ljust(26) + "name in the certificate".ljust(26) + "valid until".ljust(14) + "days left   used by")
    now = datetime.now(timezone.utc)

    for secret, key, user in CERTIFICATES:
        try:
            name, when = certificate_facts(secret, key)
        except lib.Fail as problem:
            print("   " + secret.ljust(26) + "(not readable: " + shorten(problem, 120) + ")")
            judge("the certificate in " + secret + " can be read", False)
            continue

        days = (when - now).days
        print("   " + secret.ljust(26) + name.ljust(26) + when.strftime("%Y-%m-%d").ljust(14) + str(days).rjust(9) + "   " + user)
        judge(secret + ": more than " + str(DAYS_WARNING) + " days left", days > DAYS_WARNING)

    print()
    print("When a date comes close, the certificates have to be replaced before it passes: a program with an expired certificate is refused by the others, and the pipeline stops.\n")


# ---------------------------------------------------------------- 4. rotating a certificate (not done)

def step_rotation():
    section(4, "Rotate a certificate: NOT DONE here, on purpose")
    print("With the design of this stack a certificate cannot be replaced while the data stays. Three reasons, all in processor/bootstrap.py:")
    print("  - The private key of the CA was never stored, so nobody can sign a new certificate that the old CA vouches for.")
    print("  - The Job bootstrap makes all 13 Secrets or none. When only some exist it stops with an error, because new certificates would not match the old ones.")
    print("  - The passwords are in the same 13 Secrets, and MongoDB created its users only once, when its disk was still empty (the official image runs its set-up")
    print("    scripts only for an empty data directory). A new password would not match the old disk.")
    print()
    print("So today the only way to new certificates is   make down   (deletes everything, also the disks) and   make up   (the Job makes everything new, the replay Job loads the sample flows again).")
    print("That is fine for a course project and wrong for a production system. runbook.md says what a production system does (cert-manager renews certificates before they end and the")
    print("programs reload them) and what would have to change in this stack: keep the CA key somewhere safe and separate (a tool such as cert-manager, or a Secret that only a renewal Job may read), and make the passwords separate from the certificates.")
    print("This step was not run, so nothing here claims that it works.\n")


# ---------------------------------------------------------------- the whole program

started = datetime.now(timezone.utc)
sys.stdout = Tee(sys.stdout)
problem_text = None

for _name in ("SIGINT", "SIGHUP", "SIGTERM"):
    signal.signal(getattr(signal, _name), stop_on_signal)

try:
    print("Operator steps, " + started.strftime("%Y-%m-%d %H:%M UTC") + "   (namespace " + lib.NS + ")")
    print("These are the manual steps of runbook.md, done once on the running stack. Nothing here deletes data.\n")
    problems = lib.stack_problems()

    if problems:
        raise lib.Fail("the stack is not ready: " + ", ".join(problems) + ". Run:  make status")

    step_logs()

    if args.skip_scale:
        section(2, "Scale replicas")
        print("   skipped (--skip-scale). The command is:  kubectl -n " + lib.NS + " scale deployment/restapi --replicas=3   and back with --replicas=2\n")
    else:
        step_scale()

    step_certificates()
    step_rotation()
except lib.Fail as problem:
    problem_text = " ".join(str(problem).split())
except KeyboardInterrupt:
    problem_text = "stopped by the user (Ctrl-C)" if stopped_by[:1] in ([], ["SIGINT"]) else "stopped by a signal (" + stopped_by[0] + ": the window was closed, or the program was killed)"
except Exception:
    traceback.print_exc()
    problem_text = "the program itself broke (see the error above)"
finally:
    ignore_signals()

    if changed["replicas"] is not None:
        print("\nPutting the REST API back to " + str(changed["replicas"]) + " pods before the program ends ...")

        try:
            lib.kubectl("-n", lib.NS, "scale", "deployment/restapi", "--replicas=" + str(changed["replicas"]), timeout=40)
            print("Done: kubectl scale deployment/restapi --replicas=" + str(changed["replicas"]))
        except lib.Fail as problem:
            print("COULD NOT put it back (" + shorten(problem, 150) + "). Do it by hand:  kubectl -n " + lib.NS + " scale deployment/restapi --replicas=" + str(changed["replicas"]))

sys.stdout = sys.stdout.stream if isinstance(sys.stdout, Tee) else sys.stdout
failed = [what for what, ok in checks if not ok]

try:
    out_dir.mkdir(parents=True, exist_ok=True)
    # No address is printed on purpose, but an error message could carry one: "make scrub" removes it before the commit.
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

print("\nRESULT: PASS - " + str(len(checks)) + " of " + str(len(checks)) + " checks matched (logs readable, the REST API scaled up and back, certificates valid; the certificate rotation was not run)")
