# restapi/rest_app.py against a fake MongoDB (mongomock), filled by the real MongoSink with what the real engine makes.

import base64

import mongomock
import pytest

from engine import Engine
from fakes import Broker, FakeDetectors, ShimDatabase, bgl_line, make_records, record
from sink import MongoSink
from summary import DETECTORS, summarize

AUTH = {"Authorization": "Basic " + base64.b64encode(b"reader:right-password").decode()}


@pytest.fixture(scope="module")
def api():
    patch = pytest.MonkeyPatch()
    values = {"MONGO_HOST": "mongo", "MONGO_PORT": "27017", "MONGO_DB": "nlp", "MONGO_COLLECTION": "events", "MONGO_WINDOWS_COLLECTION": "windows",
              "MONGO_RUNS_COLLECTION": "runs", "MONGO_USER": "restapi", "MONGO_PASSWORD": "db-password", "MONGO_TLS_CA_FILE": "/ca",
              "REST_USER": "reader", "REST_PASSWORD": "right-password", "REST_PORT": "8443", "REST_METRICS_PORT": "0"}

    for name, value in values.items():
        patch.setenv(name, value)

    patch.setattr("pymongo.MongoClient", mongomock.MongoClient)

    import rest_app

    rest_app.app.config["TESTING"] = False
    yield rest_app
    patch.undo()


@pytest.fixture(scope="module")
def client(api):
    return api.app.test_client()


def get(client, path, auth=AUTH):
    response = client.get(path, headers=auth)
    return response.status_code, response.get_json()


def test_the_probes_need_no_login_and_everything_else_does(client):
    assert client.get("/health").status_code == 200
    assert client.get("/ready").get_json() == {"status": "ready"}

    for path in ("/stats", "/runs", "/summary", "/windows", "/events", "/alerts", "/events/abc"):
        assert client.get(path).status_code == 401, path

    wrong = {"Authorization": "Basic " + base64.b64encode(b"reader:wrong").decode()}
    assert client.get("/stats", headers=wrong).status_code == 401
    assert client.get("/stats", headers={"Authorization": "Bearer something"}).status_code == 401


def test_before_any_replay_the_answers_say_so(client):
    assert get(client, "/runs") == (200, {"count": 0, "runs": []})

    for path in ("/summary", "/windows", "/events"):
        status, body = get(client, path)
        assert status == 404 and body["found"] is False and "no replay" in body["error"]


@pytest.fixture(scope="module")
def filled(api, client):
    sink = MongoSink(ShimDatabase(api.database))
    broker = Broker(make_records(1, n=400, run="run-one") + make_records(2, n=150, run="run-two", first_offset=401))

    # the second run starts later: the clock of the engine moves on
    ticks = iter(range(1000, 100000))
    from engine import Engine as _Engine

    detectors = FakeDetectors()
    engine = _Engine(detectors, sink.load_state, lambda: float(next(ticks)))

    for start in range(0, len(broker.records), 60):
        out = engine.process(broker.records[start: start + 60])
        sink.write(out)

    smoke = engine.process([record(1000, {"kind": "smoke", "trace_id": "smoke-77", "line": bgl_line(5000, 11, "KERNDTLB")})])
    sink.write(smoke)
    return sink


def test_stats_counts_the_documents(client, filled):
    status, body = get(client, "/stats")
    assert status == 200 and body["database"] == "nlp" and body["collection"] == "events"
    assert body["documents"] == 400 + 150 + 1
    assert body["collections"]["runs"] == 2 and body["collections"]["windows"] > 20


def test_runs_come_newest_first_without_the_restart_bookkeeping(client, filled):
    status, body = get(client, "/runs")
    runs = body["runs"]

    assert status == 200 and [r["run"] for r in runs] == ["run-two", "run-one"]
    assert runs[1]["status"] == "complete" and runs[1]["lines_stored"] == 400 and runs[1]["windows_stored"] > 10
    assert all(key not in runs[0] for key in ("origin", "next_k", "last_key", "done", "_id"))
    assert get(client, "/runs?limit=1")[1]["count"] == 1
    assert get(client, "/runs?limit=0")[0] == 400


def test_the_summary_is_the_confusion_of_each_detector_on_the_windows_of_a_run(api, client, filled):
    status, body = get(client, "/summary?run=run-one")
    expected = summarize(list(filled.windows.find({"run": "run-one"})))

    assert status == 200 and body["run"]["run"] == "run-one" and body["run"]["status"] == "complete"
    assert body["windows"] == expected["windows"] > 10 and body["anomalous_windows"] == expected["anomalous_windows"]
    assert set(body["detectors"]) == {field for field, _ in DETECTORS}

    for field, _ in DETECTORS:
        found = body["detectors"][field]
        assert found["tp"] + found["fp"] + found["tn"] + found["fn"] == body["windows"]
        assert found == expected["detectors"][field]

    assert body["lines"]["stored"] == 400 and body["lines"]["with_alert_tag"] > 0 and body["lines"]["template_not_known"] == 0
    assert body["lines"]["llm_has_no_saved_decision"] > 0

    # without ?run= the newest run
    assert get(client, "/summary")[1]["run"]["run"] == "run-two"
    assert get(client, "/summary?run=nothing")[0] == 404


def test_windows_are_in_time_order_and_can_be_filtered(client, filled):
    status, body = get(client, "/windows?run=run-one&limit=500")
    starts = [w["start"] for w in body["windows"]]

    assert status == 200 and starts == sorted(starts) and "_id" not in body["windows"][0]
    assert body["count"] == len(starts) > 10

    for flagged in ("truth", "logbert", "llm", "keyword"):
        _, found = get(client, f"/windows?run=run-one&flagged={flagged}&limit=500")
        assert found["count"] > 0 and all(w[flagged] == 1 for w in found["windows"]), flagged

    assert get(client, "/windows?run=run-one&limit=3&skip=2")[1]["windows"] == body["windows"][2:5]
    assert get(client, "/windows?flagged=bogus")[0] == 400
    assert get(client, "/windows?limit=abc")[0] == 400
    assert get(client, "/windows?limit=501")[0] == 400
    assert get(client, "/windows?run=nothing")[0] == 404


def test_events_are_in_order_and_can_be_filtered(client, filled):
    status, body = get(client, "/events?run=run-one&limit=500")
    offsets = [e["offset"] for e in body["events"]]

    assert status == 200 and offsets == sorted(offsets) and body["count"] == 400
    assert {"content", "template_id", "truth", "alert", "keyword", "llm", "node"} <= set(body["events"][0])

    _, alerts = get(client, "/events?run=run-one&alert=1&limit=500")
    assert 0 < alerts["count"] < 400 and all(e["alert"] == 1 for e in alerts["events"])

    _, one = get(client, "/events?run=run-one&template_id=7&limit=500")
    assert one["count"] > 0 and all(e["template_id"] == 7 for e in one["events"])
    assert get(client, "/events?alert=2")[0] == 400


def test_alerts_are_the_alert_lines_of_the_newest_run_and_not_the_smoke_line(client, filled):
    status, body = get(client, "/alerts")

    assert status == 200 and 0 < body["count"] <= 50
    assert all(a["alert"] == 1 and a["run"] == "run-two" and not a.get("smoke") for a in body["alerts"])


def test_the_smoke_line_is_found_by_its_trace_id(client, filled):
    status, body = get(client, "/events/smoke-77")
    assert status == 200 and body["found"] is True
    assert body["event"]["trace_id"] == "smoke-77" and body["event"]["smoke"] is True and body["event"]["alert"] == 1 and body["event"]["template_id"] == 11

    status, body = get(client, "/events/not-there")
    assert status == 404 and body == {"found": False, "trace_id": "not-there"}


def test_the_api_never_writes(api):
    import inspect
    source = inspect.getsource(api)

    for word in ("insert_one", "insert_many", "update_one", "update_many", "replace_one", "delete_one", "delete_many", "bulk_write", "drop("):
        assert word not in source, word
