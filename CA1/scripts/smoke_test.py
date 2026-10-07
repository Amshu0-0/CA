#!/usr/bin/env python3
"""End-to-end smoke test: follow ONE uniquely tagged event through the whole pipeline.

    producer VM -> Kafka -> processor -> MongoDB -> REST API

Counting rows (for example "50 alerts") cannot tell which events moved, and old data can hide a
broken step. This test creates an event that has never existed before, tags it with a random
trace id, and then checks every step on its own:

  1. The REST API answers, and does not know the event yet (a negative control).
  2. The producer sends the event and Kafka reports the partition and offset it stored it at.
  3. The processor's log shows it read that same event from that same Kafka offset.
  4. MongoDB holds exactly one document with the trace id (checked inside the database VM).
  5. The REST API now returns the event.

A test that cannot fail proves nothing, so every step prints PASS or FAIL, the script stops at the
first FAIL, and it exits with a non-zero code. It removes its own test event when it finishes.

It needs nothing but Python 3 and ssh. Hosts and the SSH key come from ansible/inventory.ini
(written by Terraform) and the settings come from config.yml, so nothing is typed in here.
"""

import argparse
import json
import re
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

CA1_FOLDER = Path(__file__).resolve().parent.parent
INVENTORY_FILE = CA1_FOLDER / "ansible" / "inventory.ini"
CONFIG_FILE = CA1_FOLDER / "config.yml"

# Every test event carries this Label, so it can never be mistaken for real traffic.
TEST_LABEL = "SMOKE-TEST"


class StepFailed(Exception):
    """Raised when a step fails, so the test can stop and clean up."""


# ---------- reading the project's own settings ----------

def read_config():
    """Read the simple 'key: value' lines of config.yml."""
    settings = {}
    for line in CONFIG_FILE.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if ":" in line:
            key, value = line.split(":", 1)
            settings[key.strip()] = value.strip()
    return settings


def read_inventory():
    """Read ansible/inventory.ini into {group: {ip, user, key}}."""
    hosts, group = {}, None
    for line in INVENTORY_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("["):
            group = line.strip("[]")
            continue
        if group and ":" not in group:
            fields = line.split()
            options = dict(item.split("=", 1) for item in fields[1:] if "=" in item)
            hosts[group] = {
                "ip": fields[0],
                "user": options.get("ansible_user", "ubuntu"),
                "key": options["ansible_ssh_private_key_file"],
            }
    return hosts


# ---------- talking to the VMs and the REST API ----------

def remote(host, command, timeout):
    """Run one shell command on a VM over ssh."""
    arguments = [
        "ssh", "-i", host["key"],
        "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
        "-o", "LogLevel=ERROR", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
        f"{host['user']}@{host['ip']}", command,
    ]
    try:
        return subprocess.run(arguments, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise StepFailed(f"ssh to {host['ip']} did not finish within {timeout} s")


def mongo_eval(database_host, javascript, timeout=60):
    """Run one line of JavaScript in mongosh inside the MongoDB container.

    The username and password are read from the container's own environment, so they never pass
    through this script, the command line, or the log.
    """
    inside_container = (
        'mongosh --quiet -u "$MONGO_INITDB_ROOT_USERNAME" -p "$MONGO_INITDB_ROOT_PASSWORD" '
        "--authenticationDatabase admin --eval " + shlex.quote(javascript)
    )
    command = "cd /home/ubuntu/mongo && sudo docker compose exec -T mongodb sh -c " + shlex.quote(inside_container)
    result = remote(database_host, command, timeout)
    lines = result.stdout.strip().splitlines()
    if result.returncode != 0 or not lines or not lines[-1].strip().isdigit():
        raise StepFailed("mongosh did not return a number: " + (result.stdout + result.stderr).strip()[-300:])
    return int(lines[-1])


def rest_get(url):
    """GET a URL and return (status code, decoded JSON or a short note)."""
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            status, text = response.status, response.read().decode()
    except urllib.error.HTTPError as error:
        status, text = error.code, error.read().decode()
    except (urllib.error.URLError, OSError) as error:
        return None, {"error": str(error)}
    try:
        return status, json.loads(text)
    except ValueError:
        return status, {"not_json": text[:60]}


def wait_for(probe, timeout):
    """Call probe() once a second until it returns something, or give up. Returns (result, seconds)."""
    started = time.time()
    while True:
        result = probe()
        if result:
            return result, time.time() - started
        if time.time() - started >= timeout:
            return None, time.time() - started
        time.sleep(1)


# ---------- reporting ----------

def check(title, ok, detail, hint=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {title:<52} {detail}")
    if not ok:
        if hint:
            print(f"         hint: {hint}")
        raise StepFailed(title)


# ---------- the test ----------

def run_test(trace_id, settings, hosts, timeout):
    producer, processor, database = hosts["producer"], hosts["processor"], hosts["database"]
    base_url = f"http://{database['ip']}:{settings['rest_port']}"
    collection_js = f'db.getSiblingDB("{settings["mongo_db"]}").getCollection("{settings["mongo_collection"]}")'

    # Step 1: the REST API is up, and it does not know this event (our endpoint answers, in JSON).
    status, body = rest_get(f"{base_url}/health")
    check("REST API answers", status == 200, f"GET /health -> {status if status is not None else 'no answer'}",
          "is the REST service running, and is your current IP allowed by the security group?")

    status, body = rest_get(f"{base_url}/events/{trace_id}")
    ours = body.get("found") is False
    check("Event is not in the database yet", status == 404 and ours,
          f"GET /events/<id> -> {status}" + (" (found: false)" if ours else " (not the REST API's own JSON answer)"),
          "the /events endpoint is missing: run ansible-playbook site.yml to deploy the new REST API")

    # Step 2: the producer sends one tagged event, and Kafka says where it stored it.
    sent_at = time.time()
    result = remote(producer, "sudo docker run --rm --env-file /etc/ca1/producer.env producer "
                              f"python -u producer.py --trace-id {trace_id}", 90)
    sent = re.search(r"sent trace_id=(\S+) topic=(\S+) partition=(\d+) offset=(\d+)", result.stdout)
    check("Producer sent it and Kafka stored it",
          result.returncode == 0 and sent is not None and sent.group(1) == trace_id,
          f"topic {sent.group(2)}, partition {sent.group(3)}, offset {sent.group(4)}" if sent
          else (result.stdout + result.stderr).strip()[-120:],
          "run ansible-playbook site.yml so the producer image has the --trace-id option")
    partition, offset = sent.group(3), sent.group(4)
    accepted_at = time.time()

    # Step 3: the processor logs the same trace id at the same Kafka partition and offset.
    def processor_saw_it():
        logs = remote(processor, f"sudo docker logs --since 15m processor 2>&1 | grep -F 'trace_id={trace_id}' || true", 30)
        return re.search(rf"\[TRACE\] trace_id={trace_id} partition=(\d+) offset=(\d+) stored", logs.stdout)

    seen, waited = wait_for(processor_saw_it, timeout)
    check("Processor read the same Kafka offset and stored it",
          seen is not None and (seen.group(1), seen.group(2)) == (partition, offset),
          f"[TRACE] partition {seen.group(1)}, offset {seen.group(2)} ({waited:.1f} s after Kafka stored it)" if seen
          else f"no [TRACE] line for this event within {timeout} s",
          "is the processor container running and subscribed to the topic?  docker logs processor")

    # Step 4: MongoDB holds exactly one document with this trace id (asked inside the database VM).
    copies = mongo_eval(database, f'{collection_js}.countDocuments({{trace_id: "{trace_id}"}})')
    check("MongoDB holds exactly one copy", copies == 1,
          f'{settings["mongo_db"]}.{settings["mongo_collection"]}: {copies} document with this trace id',
          "the processor logged it, so check which database and collection it writes to")

    # Step 5: the REST API returns the event, as an outside user would see it.
    def rest_has_it():
        code, found = rest_get(f"{base_url}/events/{trace_id}")
        return (code, found) if code == 200 else None

    answer, _ = wait_for(rest_has_it, timeout)
    event = answer[1].get("event", {}) if answer else {}
    check("REST API returns the event",
          answer is not None and event.get("trace_id") == trace_id and event.get("Label") == TEST_LABEL,
          f"GET /events/<id> -> 200, Label {event.get('Label')}" if answer else "the REST API never returned it",
          "MongoDB has it, so check that the REST API reads the same database and collection")

    return time.time() - accepted_at, time.time() - sent_at


def clean_up(trace_id, settings, hosts, say):
    """Remove every test event (this one, plus any left by an earlier failed run)."""
    collection_js = f'db.getSiblingDB("{settings["mongo_db"]}").getCollection("{settings["mongo_collection"]}")'
    removed = mongo_eval(hosts["database"], f'{collection_js}.deleteMany({{Label: "{TEST_LABEL}"}}).deletedCount')
    say(removed)
    return removed


def main():
    parser = argparse.ArgumentParser(description="Follow one uniquely tagged event through the pipeline.")
    parser.add_argument("--timeout", type=int, default=30, help="seconds to wait at each step (default 30)")
    arguments = parser.parse_args()

    settings, hosts = read_config(), read_inventory()
    trace_id = "smoke-" + uuid.uuid4().hex
    base_url = f"http://{hosts['database']['ip']}:{settings['rest_port']}"

    print("CA1 end-to-end smoke test")
    print(f"  trace id : {trace_id}")
    print("  path     : producer VM -> Kafka -> processor -> MongoDB -> REST API")
    print()

    passed, failure, timings = False, None, None
    try:
        # Remove leftovers from any earlier failed run, so they cannot confuse this one.
        clean_up(trace_id, settings, hosts, lambda n: print(f"  (removed {n} leftover test event(s) from earlier runs)") if n else None)
        timings = run_test(trace_id, settings, hosts, arguments.timeout)
        passed = True
    except StepFailed as error:
        failure = str(error)
    finally:
        # Always remove the test event, even when a step failed.
        try:
            clean_up(trace_id, settings, hosts, lambda n: None)
            status, body = rest_get(f"{base_url}/events/{trace_id}")
            print(f"  cleanup  : test event removed; REST API now answers {status}")
        except StepFailed as error:
            print(f"  cleanup  : WARNING, could not remove the test event ({error})")

    print()
    if passed:
        print(f"RESULT: PASS - one event crossed producer -> Kafka -> processor -> MongoDB -> REST API,")
        print(f"        visible through the REST API {timings[0]:.1f} s after Kafka stored it.")
        return 0

    print(f"RESULT: FAIL - stopped at: {failure}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
