# Turns the measurements of one scaling test into a results table and a chart.
#
#   make scale-report                          draw them again from the saved files (no cluster needed)
#   python3 scripts/scalereport.py --dir evidence --prefix step7-scale
#
# scripts/scale-test.py saves two files while it runs: <prefix>-samples.csv (one row every few seconds) and
# <prefix>-summary.json (when each phase started and ended, the tagged-event timings, the autoscaler settings).
# This program reads them and writes <prefix>-results.md (the table) and <prefix>-chart.svg (the chart).
# It uses only the standard library, so it runs anywhere python3 runs.

import argparse
import csv
import json
import math
import sys
from pathlib import Path
from statistics import mean, median
from xml.sax.saxutils import escape

# The columns of the samples file, in order. scale-test.py writes them and this program reads them.
COLUMNS = ["t", "clock", "phase",
           "producer_pods_ready", "producer_pods_wanted", "producer_cpu_pct",
           "processor_pods_ready", "processor_pods_wanted", "processor_cpu_pct",
           "producers_msg_s", "sent_total", "docs", "kafka_end", "kafka_lag",
           "agent_cpu_pct", "server_cpu_pct", "restarts"]
TEXT = ("clock", "phase")


# ---------------------------------------------------------------- reading the saved files

def load(directory, prefix):
    rows = []

    with open(Path(directory) / (prefix + "-samples.csv"), newline="") as f:
        for raw in csv.DictReader(f):
            row = {}

            for key in COLUMNS:
                value = raw.get(key, "")
                row[key] = value if key in TEXT else (float(value) if value not in ("", None) else None)

            rows.append(row)

    summary = json.loads((Path(directory) / (prefix + "-summary.json")).read_text())

    return rows, summary


# ---------------------------------------------------------------- the numbers of one measuring window

# A counter that only grows (records in MongoDB, offsets in Kafka) becomes a rate: last value minus first value,
# divided by the time between them. Needs two values that are at least min_span seconds apart, otherwise the answer is None.
def counter_rate(points, min_span):
    points = [p for p in points if p[1] is not None]

    if len(points) < 2 or points[-1][0] - points[0][0] < min_span:
        return None

    return (points[-1][1] - points[0][1]) / (points[-1][0] - points[0][0])


def average(values):
    values = [v for v in values if v is not None]
    return mean(values) if values else None


# The smallest and the largest value seen in a window, or None if the value never changed.
def changed_between(values):
    values = [v for v in values if v is not None]

    return (min(values), max(values)) if values and min(values) != max(values) else None


def window_numbers(rows, t0, t1):
    inside = [r for r in rows if t0 <= r["t"] <= t1]
    span = 0.5 * (t1 - t0)       # the values used must cover at least half of the window
    stored = counter_rate([(r["t"], r["docs"]) for r in inside], span)
    lags = [r for r in inside if r["kafka_lag"] is not None]
    lag = lags[-1]["kafka_lag"] if lags else None

    return {
        "samples": len(inside),
        "producer_pods": median([r["producer_pods_ready"] for r in inside if r["producer_pods_ready"] is not None]) if inside else None,
        "processor_pods": median([r["processor_pods_ready"] for r in inside if r["processor_pods_ready"] is not None]) if inside else None,
        "producers_msg_s": average([r["producers_msg_s"] for r in inside]),
        "kafka_msg_s": counter_rate([(r["t"], r["kafka_end"]) for r in inside], span),
        "stored_msg_s": stored,
        "lag": lag,
        "wait_s": (lag / stored) if (lag is not None and stored and stored > 0) else None,
        "producer_cpu_pct": average([r["producer_cpu_pct"] for r in inside]),
        "processor_cpu_pct": average([r["processor_cpu_pct"] for r in inside]),
        "agent_cpu_pct": average([r["agent_cpu_pct"] for r in inside]),
        "server_cpu_pct": average([r["server_cpu_pct"] for r in inside]),
        "processor_pods_changed": changed_between([r["processor_pods_ready"] for r in inside]),
    }


# When a phase of the test began and ended (None if it never finished).
def phase_times(summary, name):
    found = [p for p in summary.get("phases", []) if p["name"] == name and p.get("t1") is not None]
    return (found[0]["t0"], found[0]["t1"]) if found else None


# The two measuring windows are saved separately from the phases: a phase can last longer than its window.
def window_times(summary, name):
    found = summary.get("windows", {}).get(name)
    return (found["t0"], found["t1"]) if found else None


# ---------------------------------------------------------------- formatting

def num(value, digits=0):
    if value is None:
        return "n/a"

    return format(value, ",." + str(digits) + "f")


def pods(value):
    return "n/a" if value is None else str(int(round(value)))


def times(after, before):
    if after is None or before is None or before <= 0:
        return ""

    return "x" + format(after / before, ".1f")


# ---------------------------------------------------------------- the table

def timeline(rows, summary):
    ramp = phase_times(summary, "ramp")

    if not ramp:
        return []

    start = ramp[0]
    after = window_times(summary, "after")
    end = after[0] if after else ramp[1]
    span = [r for r in rows if start <= r["t"] <= end]
    step = 10 if end - start <= 150 else 15 if end - start <= 220 else 30
    out, next_mark = [], 0

    for r in span:
        if r["t"] - start >= next_mark:
            out.append(r)
            next_mark = (int((r["t"] - start) // step) + 1) * step

    return out


def results_table(rows, summary):
    before_t, after_t = window_times(summary, "before"), window_times(summary, "after")
    b = window_numbers(rows, *before_t) if before_t else None
    a = window_numbers(rows, *after_t) if after_t else None
    probes = summary.get("probes", {})
    hpa = summary.get("hpa", {})
    lines = []
    lines.append("# Step 7: scaling test results")
    lines.append("")
    lines.append("Run started " + summary.get("started", "?") + " (UTC). The load is the Deployment `producer-load`: producer pods that replay the dataset in a loop.")

    if hpa:
        lines.append("Autoscaler `producer-load`: " + str(hpa.get("min")) + " to " + str(hpa.get("max")) + " pods, target " + str(hpa.get("cpu_target")) + "% of the CPU request."
                     + ("  Processor autoscaler: " + str(hpa["processor_min"]) + " to " + str(hpa["processor_max"]) + " pods, target " + str(hpa["processor_target"]) + "%." if hpa.get("processor_max") else ""))

    lines.append("")

    if summary.get("aborted"):
        lines.append("**The test stopped early:** " + summary["aborted"])
        lines.append("")

    lines.append("## Before and after")
    lines.append("")

    if not b and not a:
        lines.append("No measuring window was completed, so there is no table. See the sample file for what was recorded.")
    else:
        bn = int(round(b["producer_pods"])) if b and b["producer_pods"] is not None else None
        an = int(round(a["producer_pods"])) if a and a["producer_pods"] is not None else None
        lines.append("| Measurement | Before: " + (str(bn) if bn else "1") + " producer pod, no autoscaler | After: " + (str(an) if an else "?") + " producer pods, autoscaler working | Change |")
        lines.append("|---|---:|---:|---:|")

        def row(label, key, digits=0, change=True):
            bv, av = (b or {}).get(key), (a or {}).get(key)
            lines.append("| " + label + " | " + num(bv, digits) + " | " + num(av, digits) + " | " + (times(av, bv) if change else "") + " |")

        lines.append("| Producer pods ready | " + pods((b or {}).get("producer_pods")) + " | " + pods((a or {}).get("producer_pods")) + " | " + times((a or {}).get("producer_pods"), (b or {}).get("producer_pods")) + " |")
        per_pod = lambda w: (w["producers_msg_s"] / w["producer_pods"]) if (w and w["producers_msg_s"] is not None and w["producer_pods"]) else None
        lines.append("| Messages per second from one producer pod | " + num(per_pod(b)) + " | " + num(per_pod(a)) + " | |")
        row("Producers send, messages per second (all pods together, reported by the pods)", "producers_msg_s")
        row("Kafka receives, messages per second (growth of the topic)", "kafka_msg_s")
        row("MongoDB stores, messages per second (growth of the record count)", "stored_msg_s")
        row("Backlog in Kafka at the end of the window, messages", "lag", change=False)
        row("Time a new message would wait in the backlog, seconds (backlog / storing rate)", "wait_s", 1, change=False)
        bp, ap = probes.get("before") or {}, probes.get("after") or {}
        lines.append("| One tagged event: stored in Kafka until the REST API returns it, seconds | " + num(bp.get("seconds"), 1) + " | " + num(ap.get("seconds"), 1) + " | |")
        lines.append("| Processor pods ready | " + pods((b or {}).get("processor_pods")) + " | " + pods((a or {}).get("processor_pods")) + " | " + times((a or {}).get("processor_pods"), (b or {}).get("processor_pods")) + " |")
        row("Producer pods' CPU, % of their request (from the autoscaler)", "producer_cpu_pct", change=False)
        row("Processor pods' CPU, % of their request (from their autoscaler)", "processor_cpu_pct", change=False)
        row("Worker nodes' CPU, % (average of the workers, from kubectl top nodes)", "agent_cpu_pct", change=False)
        row("Control-plane node's CPU, % (the server also runs pods, from kubectl top nodes)", "server_cpu_pct", change=False)

    steps = timeline(rows, summary)

    if steps:
        lines.append("")
        lines.append("## How the autoscaler added pods")
        lines.append("")
        lines.append("| Seconds after the autoscaler was created | Producer pods ready | Producer pods wanted by the autoscaler | Producer pods' CPU, % of request | Processor pods ready |")
        lines.append("|---:|---:|---:|---:|---:|")
        start = phase_times(summary, "ramp")[0]

        for r in steps:
            lines.append("| " + str(int(round(r["t"] - start))) + " | " + pods(r["producer_pods_ready"]) + " | " + pods(r["producer_pods_wanted"]) + " | " + num(r["producer_cpu_pct"]) + " | " + pods(r["processor_pods_ready"]) + " |")

    lines.append("")
    note = ("The tagged event is the one the smoke test sends. Its time runs from the moment the producer Job reported that Kafka had stored it until the REST API returned it (asked every second),"
            " so it is accurate to about 2 seconds.")

    if window_times(summary, "after"):
        note += " The second event was sent in the middle of the second window; the load was removed at the end of that window."

    if summary.get("trace_index"):
        note += " The REST API finds the event by its trace_id; before the test the script gave MongoDB a small index on that field, so the lookup does not read every stored record."

    lines.append(note)
    lines.append("")
    lines.append("## What the numbers say")
    lines.append("")
    lines.extend(conclusions(b, a, summary, rows))
    placement = summary.get("load_pods_per_node")

    if placement:
        lines.append("- At the end the producer pods ran on: " + ", ".join(k + " " + str(v) for k, v in sorted(placement.items())) + ".")

    lines.append("")
    lines.append("![chart](" + summary.get("chart_name", "step7-scale-chart.svg") + ")")
    lines.append("")

    return "\n".join(lines)


# Sentences that are true for this run. Each one is written only when the numbers support it.
def conclusions(b, a, summary, rows):
    out = []
    probes = summary.get("probes", {})

    if b and a and b["producers_msg_s"] and a["producers_msg_s"]:
        ratio = a["producers_msg_s"] / b["producers_msg_s"]
        out.append("- The producers' combined rate went from " + num(b["producers_msg_s"]) + " to " + num(a["producers_msg_s"]) + " messages per second (x" + format(ratio, ".1f") + ") when the autoscaler took the producers from "
                   + pods(b["producer_pods"]) + " to " + pods(a["producer_pods"]) + " pods.")

        pods_ratio = (a["producer_pods"] / b["producer_pods"]) if b["producer_pods"] else None

        if pods_ratio and ratio < 0.8 * pods_ratio:
            workers = a["agent_cpu_pct"]
            less = "- That is less than " + format(pods_ratio, ".0f") + " times the rate of one pod"

            if workers is not None and workers >= 85:
                out.append(less + ". The worker nodes were nearly full (" + num(workers) + "% CPU on average), and the producer pods share them with Kafka, MongoDB and the processors.")
            elif workers is not None:
                out.append(less + ". The worker nodes averaged " + num(workers) + "% CPU, so they were busy but not full. This test did not measure what held the producers back"
                           " (the CPU limit of each pod, the shared nodes or Kafka), so no cause is claimed.")
            else:
                out.append(less + ". There are no CPU numbers for the workers, so the cause is not known.")

    if b and b.get("processor_pods_changed"):
        low, high = b["processor_pods_changed"]
        extra = ""

        if b["stored_msg_s"] is not None and b["kafka_msg_s"] and b["stored_msg_s"] > 1.05 * b["kafka_msg_s"]:
            extra = (" MongoDB stored more per second (" + num(b["stored_msg_s"]) + ") than Kafka received (" + num(b["kafka_msg_s"])
                     + "), which is only possible while the processors work off messages that were already waiting.")

        out.append("- The processors were still scaling during the 'before' window (from " + pods(low) + " to " + pods(high) + " pods), so 'before' is not a steady state." + extra)

    if a and a["stored_msg_s"] is not None and a["producers_msg_s"]:
        if a["stored_msg_s"] < 0.9 * a["producers_msg_s"]:
            hpa = summary.get("hpa", {})
            target, busy = hpa.get("processor_target"), a["processor_cpu_pct"]
            line = ("- MongoDB stored " + num(a["stored_msg_s"]) + " messages per second while the producers sent " + num(a["producers_msg_s"]) + ". The difference waits in Kafka"
                    + (" (" + num(a["lag"]) + " messages at the end of the window)" if a["lag"] is not None else "")
                    + ", so after the producers the next limit is the part that reads from Kafka and writes to MongoDB: "
                    + (pods(a["processor_pods"]) + " processor pods" if a["processor_pods"] is not None else "the processor pods") + " and MongoDB.")

            if busy is not None and target:
                if busy >= target:
                    line += (" The processors were busy (" + num(busy) + "% of their CPU request, the target is " + str(target) + "%)"
                             + ("; their autoscaler stops at " + str(hpa["processor_max"]) + " pods, one per partition of the topic." if hpa.get("processor_max") else "."))
                else:
                    line += " The processors were not short of CPU (" + num(busy) + "% of their request, the target is " + str(target) + "%): they were waiting for MongoDB or for Kafka."

            out.append(line)
        else:
            out.append("- MongoDB kept up: it stored " + num(a["stored_msg_s"]) + " messages per second against " + num(a["producers_msg_s"]) + " sent.")

    started = [r for r in rows if (r["producer_pods_ready"] or 0) >= 1]

    if started:
        first = started[0]
        base = first["processor_pods_ready"]
        peak = max((r["processor_pods_ready"] or 0) for r in rows)
        grown = [r for r in rows if r["t"] > first["t"] and base is not None and (r["processor_pods_ready"] or 0) > base]

        if grown and peak > base:
            target = summary.get("hpa", {}).get("processor_target")
            busy = b["processor_cpu_pct"] if b else None
            why = (" (their CPU was " + num(busy) + "% of the request, the target is " + str(target) + "%)") if (busy is not None and target) else ""
            out.append("- The processors have an autoscaler of their own. It took them from " + pods(base) + " to " + pods(peak) + " pods, the first extra pod " + format(grown[0]["t"] - first["t"], ".0f")
                       + " s after the first producer pod was ready" + why + ".")

    bp, ap = (probes.get("before") or {}).get("seconds"), (probes.get("after") or {}).get("seconds")

    if bp is not None and ap is not None:
        queued = ap > 3 * max(bp, 1.0) and a is not None and (a["lag"] or 0) > 1000
        out.append("- A tagged event needed " + num(bp, 1) + " s to get from Kafka to the REST API before scaling and " + num(ap, 1) + " s after" + (", because it queued behind the backlog." if queued else "."))

    ramp = phase_times(summary, "ramp")
    after = window_times(summary, "after")
    hpa = summary.get("hpa", {})

    if ramp and hpa.get("max") and summary.get("reached_max_after") is not None:
        out.append("- The autoscaler needed " + format(summary["reached_max_after"], ".0f") + " s from its creation to have all " + str(hpa["max"]) + " producer pods ready.")
    elif ramp and after and hpa.get("max"):
        out.append("- The autoscaler did not get all " + str(hpa["max"]) + " producer pods ready within the time limit; the second window shows what it had reached.")

    if not out:
        out.append("- There were not enough measurements to draw a conclusion. Read the sample file.")

    return out


# ---------------------------------------------------------------- the chart (an SVG file written by hand)

def nice_top(value):
    if value <= 0:
        return 1

    exponent = math.floor(math.log10(value))
    base = value / 10 ** exponent

    for step in (1, 2, 2.5, 5, 10):
        if base <= step:
            return step * 10 ** exponent

    return 10 ** (exponent + 1)


# How many grid lines a panel gets, so that the steps are round numbers (a top of 5000 gives steps of 1000).
def divisions(top):
    mantissa = top / 10 ** math.floor(math.log10(top))
    return 4 if abs(mantissa - 2) < 1e-9 else 5


def short(value):
    if value >= 10000:
        return format(value / 1000, ".0f") + "k"

    if value >= 1000:
        return format(value / 1000, ".1f").rstrip("0").rstrip(".") + "k"

    return format(value, ".0f")


def stored_rate_series(rows):
    # Messages stored per second, from the record count of three samples ago to now. Averaging over a few samples
    # smooths out the jitter of one request being a little earlier or later than the next.
    points = [(r["t"], r["docs"]) for r in rows if r["docs"] is not None]
    out = []

    for i in range(2, len(points)):
        dt = points[i][0] - points[i - 2][0]

        if dt > 0:
            out.append((points[i][0], max(0.0, (points[i][1] - points[i - 2][1]) / dt)))

    return out


COLORS = {"producers": "#1f77b4", "stored": "#d95f02", "producer_pods": "#1f77b4", "processor_pods": "#2ca02c", "wanted": "#7f9fc9", "backlog": "#7b3294"}


def chart(rows, summary):
    if not rows:
        return None

    W, left, right = 980, 78, 22
    plot_w = W - left - right
    tmax = max(10.0, math.ceil(max(r["t"] for r in rows) / 30.0) * 30.0)
    x = lambda t: left + t / tmax * plot_w
    panels = [(122, 170), (338, 130), (512, 150)]
    H = 736
    out = []
    out.append('<svg xmlns="http://www.w3.org/2000/svg" width="' + str(W) + '" height="' + str(H) + '" viewBox="0 0 ' + str(W) + " " + str(H) + '" font-family="Helvetica, Arial, sans-serif" font-size="12">')
    out.append('<rect width="' + str(W) + '" height="' + str(H) + '" fill="#ffffff"/>')
    out.append('<text x="' + str(left) + '" y="24" font-size="17" font-weight="bold" fill="#222">Step 7: the autoscaler adds producer pods, and the pipeline follows</text>')
    out.append('<text x="' + str(left) + '" y="43" fill="#555">Each point is one sample of the running test. Shaded areas are the phases of the test.</text>')

    # The phases, as shaded areas behind all three panels, with their names in a row above.
    groups = [("1 producer pod, no autoscaler", ("warmup", "before"), "#e4e8ec"),
              ("autoscaler adds producer pods", ("ramp", "settle"), "#f8e9c1"),
              ("autoscaler at its maximum", ("after",), "#cfe2f6"),
              ("load removed, backlog drains", ("cleanup", "drain", "cooldown"), "#ecd9f2")]
    swatch = left

    for label, names, color in groups:
        spans = [p for p in summary.get("phases", []) if p["name"] in names]

        if not spans:
            continue

        t0 = min(p["t0"] for p in spans)
        t1 = max((p["t1"] if p.get("t1") is not None else tmax) for p in spans)
        out.append('<rect x="' + format(x(t0), ".1f") + '" y="86" width="' + format(max(1.0, x(t1) - x(t0)), ".1f") + '" height="' + str(panels[-1][0] + panels[-1][1] - 86) + '" fill="' + color + '" fill-opacity="0.55"/>')
        out.append('<rect x="' + str(swatch) + '" y="56" width="14" height="14" fill="' + color + '" stroke="#999" stroke-width="0.5"/>')
        out.append('<text x="' + str(swatch + 20) + '" y="68" fill="#333" font-size="12">' + escape(label) + "</text>")
        swatch += 20 + 6.3 * len(label) + 22

    for name, label in (("before", "measuring window: before"), ("after", "measuring window: after")):
        found = window_times(summary, name)

        if found:
            out.append('<rect x="' + format(x(found[0]), ".1f") + '" y="90" width="' + format(max(2.0, x(found[1]) - x(found[0])), ".1f") + '" height="5" fill="#444"/>')
            out.append('<text x="' + format(x(found[0]), ".1f") + '" y="87" font-size="10.5" fill="#444">' + escape(label if x(found[1]) - x(found[0]) > 150 else name) + "</text>")

    def frame(index, title, ymax, ticks, fmt):
        top, height = panels[index]
        out.append('<text x="' + str(left) + '" y="' + str(top - 8) + '" font-size="13" font-weight="bold" fill="#222">' + escape(title) + "</text>")

        for i in range(ticks + 1):
            value = ymax * i / ticks
            y = top + height - value / ymax * height
            out.append('<line x1="' + str(left) + '" y1="' + format(y, ".1f") + '" x2="' + str(W - right) + '" y2="' + format(y, ".1f") + '" stroke="#d5d9de" stroke-width="1"/>')
            out.append('<text x="' + str(left - 8) + '" y="' + format(y + 4, ".1f") + '" text-anchor="end" fill="#555">' + fmt(value) + "</text>")

        out.append('<line x1="' + str(left) + '" y1="' + str(top + height) + '" x2="' + str(W - right) + '" y2="' + str(top + height) + '" stroke="#555" stroke-width="1"/>')

    def line(index, ymax, points, color, width=2.0, dash=None, step=False, dots=False):
        top, height = panels[index]
        pts = [(x(t), top + height - min(v, ymax) / ymax * height) for t, v in points]

        if not pts:
            return

        if step:
            path = "M" + format(pts[0][0], ".1f") + " " + format(pts[0][1], ".1f")

            for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
                path += " L" + format(x1, ".1f") + " " + format(y0, ".1f") + " L" + format(x1, ".1f") + " " + format(y1, ".1f")
        else:
            path = "M" + " L".join(format(px, ".1f") + " " + format(py, ".1f") for px, py in pts)

        out.append('<path d="' + path + '" fill="none" stroke="' + color + '" stroke-width="' + str(width) + '"' + (' stroke-dasharray="' + dash + '"' if dash else "") + ' stroke-linejoin="round"/>')

        if dots:
            for px, py in pts:
                out.append('<circle cx="' + format(px, ".1f") + '" cy="' + format(py, ".1f") + '" r="3" fill="' + color + '"/>')

    def legend(index, items):
        top, height = panels[index]
        cursor = left + 190

        for label, color, dash in items:
            out.append('<line x1="' + str(cursor) + '" y1="' + str(top - 12) + '" x2="' + str(cursor + 22) + '" y2="' + str(top - 12) + '" stroke="' + color + '" stroke-width="2.5"' + (' stroke-dasharray="' + dash + '"' if dash else "") + "/>")
            out.append('<text x="' + str(cursor + 28) + '" y="' + str(top - 8) + '" fill="#333">' + escape(label) + "</text>")
            cursor += 28 + 6.4 * len(label) + 22

    # Panel 1: messages per second.
    rate_points = [(r["t"], r["producers_msg_s"]) for r in rows if r["producers_msg_s"] is not None]
    stored_points = stored_rate_series(rows)
    top1 = nice_top(max(max([v for _, v in rate_points + stored_points] or [0]), 4) * 1.08)
    frame(0, "Messages per second", top1, divisions(top1), short)
    line(0, top1, rate_points, COLORS["producers"])
    line(0, top1, stored_points, COLORS["stored"])
    legend(0, [("producers send (reported by the pods)", COLORS["producers"], None), ("MongoDB stores", COLORS["stored"], None)])

    # Panel 2: pods.
    top2 = int(max(2, max([r["producer_pods_ready"] or 0 for r in rows] + [r["producer_pods_wanted"] or 0 for r in rows] + [r["processor_pods_ready"] or 0 for r in rows])))
    frame(1, "Pods", top2, top2, lambda v: format(v, ".0f"))
    line(1, top2, [(r["t"], r["producer_pods_wanted"]) for r in rows if r["producer_pods_wanted"] is not None], COLORS["wanted"], 1.8, "5 4", step=True)
    line(1, top2, [(r["t"], r["producer_pods_ready"]) for r in rows if r["producer_pods_ready"] is not None], COLORS["producer_pods"], 2.4, step=True)
    line(1, top2, [(r["t"], r["processor_pods_ready"]) for r in rows if r["processor_pods_ready"] is not None], COLORS["processor_pods"], 2.4, step=True)
    legend(1, [("producer pods ready", COLORS["producer_pods"], None), ("producer pods the autoscaler wants", COLORS["wanted"], "5 4"), ("processor pods ready", COLORS["processor_pods"], None)])

    # Panel 3: the backlog.
    lag_points = [(r["t"], r["kafka_lag"]) for r in rows if r["kafka_lag"] is not None]
    backlog_title = "Backlog: messages in Kafka that the processors have not stored yet"

    if lag_points:
        top3 = nice_top(max(max(v for _, v in lag_points), 4) * 1.08)      # at least 0 to 5, so that the steps are whole numbers
        frame(2, backlog_title, top3, divisions(top3), short)
        line(2, top3, lag_points, COLORS["backlog"], 2.0, dots=True)
    else:
        frame(2, backlog_title, 4, 4, lambda v: "")
        out.append('<text x="' + str(left + plot_w / 2) + '" y="' + str(panels[2][0] + panels[2][1] / 2) + '" text-anchor="middle" fill="#777" font-style="italic">no data: the numbers from Kafka\'s own tools were not available in this run</text>')

    # The time axis.
    bottom = panels[-1][0] + panels[-1][1]
    step = 30 if tmax <= 360 else 60 if tmax <= 900 else 120
    t = 0

    while t <= tmax:
        out.append('<line x1="' + format(x(t), ".1f") + '" y1="' + str(bottom) + '" x2="' + format(x(t), ".1f") + '" y2="' + str(bottom + 5) + '" stroke="#555"/>')
        out.append('<text x="' + format(x(t), ".1f") + '" y="' + str(bottom + 19) + '" text-anchor="middle" fill="#555">' + str(t) + "</text>")
        t += step

    out.append('<text x="' + str(left + plot_w / 2) + '" y="' + str(bottom + 40) + '" text-anchor="middle" fill="#333">seconds since the start of the test</text>')
    out.append("</svg>")

    return "\n".join(out) + "\n"


# ---------------------------------------------------------------- writing the files

def render(directory, prefix):
    rows, summary = load(directory, prefix)
    summary["chart_name"] = prefix + "-chart.svg"
    text = results_table(rows, summary)
    (Path(directory) / (prefix + "-results.md")).write_text(text)
    drawing = chart(rows, summary)

    if drawing:
        (Path(directory) / (prefix + "-chart.svg")).write_text(drawing)

    return text


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Draw the table and the chart of a saved scaling test again.")
    parser.add_argument("--dir", default="evidence", help="the folder with the saved files (default: evidence)")
    parser.add_argument("--prefix", default="step7-scale", help="the start of the file names (default: step7-scale)")
    args = parser.parse_args()

    try:
        print(render(args.dir, args.prefix))
    except FileNotFoundError as problem:
        sys.exit("Cannot read " + str(problem.filename) + ". Run  make scale-test  first, or give the folder with --dir.")

    print("Wrote " + args.prefix + "-results.md and " + args.prefix + "-chart.svg in " + args.dir)
