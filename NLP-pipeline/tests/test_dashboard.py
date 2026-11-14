# The Grafana dashboard (observability/grafana/dashboards/pipeline.json) against the programs that feed it. Grafana and Prometheus are not
# run here, so what is checked is what can be checked without them: that every number the dashboard asks for is a number that a program
# of this stack really exposes (a left-over from another stack would show an empty panel and nothing else), that the panels fit on the
# page without lying on top of each other, and that the data sources and the search box agree with the rest of the stack.

import json
import re

import pytest

from conftest import PIPELINE

DASHBOARD = PIPELINE / "observability" / "grafana" / "dashboards" / "pipeline.json"
PROGRAMS = ("processor/processor.py", "producer/producer.py", "restapi/rest_app.py")

# Numbers that the two exporters and Prometheus itself make (their names are theirs, not ours).
FROM_EXPORTERS = {"up", "kafka_consumergroup_lag", "mongodb_ss_opcounters", "mongodb_ss_connections", "mongodb_up"}
# Words of the query language and names of labels: they are in an expression but they are not numbers.
WORDS = {"sum", "rate", "increase", "histogram_quantile", "vector", "time", "max", "min", "by", "le", "or", "and", "without", "app", "le",
         "topic", "status", "detector", "legacy_op_type", "conn_type", "job", "group", "instance", "level", "namespace", "trace"}


def dashboard():
    return json.loads(DASHBOARD.read_text(encoding="utf-8"))


def charts():
    return [p for p in dashboard()["panels"] if p["type"] != "row"]


# What the programs expose, as Prometheus shows it: a histogram is three series, a counter and a gauge are one.
def exposed():
    names = set(FROM_EXPORTERS)

    for program in PROGRAMS:
        for kind, name in re.findall(r'\b(Counter|Gauge|Histogram)\(\s*"([a-z_]+)"', (PIPELINE / program).read_text(encoding="utf-8")):
            names |= {name + suffix for suffix in ("_bucket", "_sum", "_count")} if kind == "Histogram" else {name}

    return names


def numbers_in(expression):
    expression = re.sub(r'"[^"]*"', '""', expression)     # label values
    expression = re.sub(r"\$\w+", "", expression)          # $__rate_interval
    expression = re.sub(r"\[[^\]]*\]", "", expression)     # [1m]

    return {word for word in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", expression) if word not in WORDS}


def test_the_dashboard_is_the_one_the_checks_look_for():
    d = dashboard()

    assert d["title"] == "NLP log pipeline" and d["uid"] == "nlp-pipeline"
    assert [v["name"] for v in d["templating"]["list"]] == ["trace"]
    assert len(charts()) >= 25


def test_every_number_on_the_dashboard_is_exposed_by_a_program_of_the_stack():
    known = exposed()
    unknown = {}

    for panel in charts():
        if panel["datasource"]["type"] != "prometheus":
            continue

        for target in panel["targets"]:
            for number in numbers_in(target["expr"]) - known:
                unknown.setdefault(number, panel["title"])

    assert not unknown, "numbers that no program exposes: " + ", ".join("%s (panel '%s')" % (n, t) for n, t in unknown.items())


def test_the_numbers_of_the_new_processor_are_on_the_dashboard():
    # the dashboard has to show what this stack is about: windows, the three detectors, LogBERT's time, and the lines that needed care
    asked = " ".join(t["expr"] for p in charts() for t in p["targets"])

    for number in ("processor_windows_total", "processor_windows_flagged_total", "processor_window_score_seconds_bucket", "processor_unseen_lines_total",
                   "processor_late_lines_total", "processor_open_window_lines", "producer_messages_sent_total", "kafka_consumergroup_lag", "restapi_request_duration_seconds_bucket"):
        assert number in asked, number


def test_the_detectors_on_the_dashboard_are_the_ones_the_processor_counts():
    source = (PIPELINE / "processor" / "processor.py").read_text(encoding="utf-8")
    counted = set(re.findall(r'"(\w+)"', re.search(r"for detector in \(([^)]*)\)", source).group(1)))
    asked = set(re.findall(r'detector="(\w+)"', " ".join(t["expr"] for p in charts() for t in p["targets"])))

    assert counted == {"keyword", "llm", "logbert"} and asked == counted


def test_what_belongs_to_another_stack_is_gone():
    text = DASHBOARD.read_text(encoding="utf-8").lower()

    for word in ("autoscal", "hpa", "kube_horizontalpodautoscaler", "load generator", "benign", "producer-load", "replicas"):
        assert word not in text, word


def test_the_panels_fit_on_the_page_and_do_not_lie_on_top_of_each_other():
    boxes = [(p["gridPos"]["x"], p["gridPos"]["y"], p["gridPos"]["w"], p["gridPos"]["h"], p["title"]) for p in dashboard()["panels"]]

    for x, y, w, h, title in boxes:
        assert 0 <= x and x + w <= 24 and w > 0 and h > 0, title

    for i, (x1, y1, w1, h1, t1) in enumerate(boxes):
        for x2, y2, w2, h2, t2 in boxes[i + 1:]:
            assert x1 + w1 <= x2 or x2 + w2 <= x1 or y1 + h1 <= y2 or y2 + h2 <= y1, t1 + " overlaps " + t2


def test_a_row_title_comes_before_its_panels_and_the_ids_are_unique():
    panels = dashboard()["panels"]
    ids = [p["id"] for p in panels]
    ordered = sorted(panels, key=lambda p: (p["gridPos"]["y"], p["gridPos"]["x"]))

    assert len(set(ids)) == len(ids) and ordered[0]["type"] == "row"

    # every chart lies below the row that owns it: no chart sits above the first row, and rows are one line high
    assert all(p["gridPos"]["h"] == 1 for p in panels if p["type"] == "row")


def test_the_data_sources_are_the_ones_grafana_is_given():
    provisioning = (PIPELINE / "observability" / "grafana.yaml").read_text(encoding="utf-8")
    uids = set(re.findall(r"^\s*uid:\s*(\w+)", provisioning, re.M))
    used = {(p["datasource"]["type"], p["datasource"]["uid"]) for p in charts()} | {(t["datasource"]["type"], t["datasource"]["uid"]) for p in charts() for t in p["targets"]}

    assert {"prometheus", "loki"} <= uids
    assert used == {("prometheus", "prometheus"), ("loki", "loki")}


def test_the_search_box_of_the_log_panel_is_the_variable_the_dashboard_defines():
    logs = [p for p in charts() if p["type"] == "logs" and "$trace" in p["targets"][0]["expr"]]

    assert len(logs) == 1 and logs[0]["options"]["sortOrder"] == "Ascending"


def test_the_dashboard_file_is_what_the_kustomization_and_the_checks_name():
    kustomization = (PIPELINE / "observability" / "kustomization.yaml").read_text(encoding="utf-8")
    metrics_check = (PIPELINE / "scripts" / "metrics-check.py").read_text(encoding="utf-8")

    assert "pipeline.json=grafana/dashboards/pipeline.json" in kustomization
    assert "NLP log pipeline" in metrics_check
