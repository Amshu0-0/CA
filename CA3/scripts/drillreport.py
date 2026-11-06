# Turns the numbers of one resilience drill into a results table, a list of goals met or not met, and a chart.
#
#   make drill-report                          draw them again from the saved files (no cluster needed)
#   python3 scripts/drillreport.py --out evidence --prefix step7-drill
#
# scripts/drill.py saves <prefix>-summary.json while it runs: for each tier that was drilled, when the pod was deleted, how many seconds until
# the new pod was ready, until the whole stack was whole again, until a test event could be read from the REST API, what the checks of the
# data found, and how the REST API behaved meanwhile. This program reads that file and writes <prefix>-results.md (the table),
# <prefix>-goals.json (the goals and the verdicts) and <prefix>-chart.svg.
# The goals are the entries of slo.json (one folder up) whose field "test" says drill. Change a number there (make goals), run this program
# again, and the verdicts change: the measurements stay the same.
# It uses only the standard library, so it runs anywhere python3 runs.

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape

GOALS_FILE = Path(__file__).resolve().parent.parent / "slo.json"

# The tiers in the order the drill runs them, and how a reader should call them.
ORDER = ("kafka", "processor", "mongo", "restapi", "producer")
NAMES = {"kafka": "Kafka", "processor": "Processor", "mongo": "MongoDB", "restapi": "REST API", "producer": "Producer"}


# ---------------------------------------------------------------- reading the saved file

def load(directory, prefix):
    return json.loads((Path(directory) / (prefix + "-summary.json")).read_text())


# The goals that make drill measures: the entries of slo.json whose field "test" says drill.
def objectives():
    try:
        found = json.loads(GOALS_FILE.read_text())["objectives"]
    except (OSError, ValueError, KeyError):
        return []

    return [o for o in found if o.get("test") == "drill"]


# Does a measured number reach the goal? An "at least" goal needs a number that is as large, an "at most" goal a number that is as small.
def meets(o, value):
    return value >= o["goal"] if o.get("direction") == "at least" else value <= o["goal"]


def goal_text(o):
    value = o["goal"]
    return ("at least " if o.get("direction") == "at least" else "at most ") + (format(value, ",") if isinstance(value, (int, float)) else str(value)) + " " + o.get("unit", "")


# ---------------------------------------------------------------- formatting

def seconds(value):
    if value is None:
        return "n/a"

    return format(value, ",.0f") + " s" + ((" (" + format(value / 60.0, ".1f") + " min)") if value >= 120 else "")


def name_of(tier):
    return NAMES.get(tier, tier)


def drilled(summary):
    return [t for t in summary.get("tiers", []) if t.get("deleted")]


# ---------------------------------------------------------------- the goals

# One verdict per goal in slo.json that this drill can measure. Each is a dict: id, text, goal (as text), measured (as text), verdict ("met", "NOT met", "not measured").
def judge(summary):
    goals = {o["id"]: o for o in objectives()}
    tiers = drilled(summary)
    out = []

    def add(oid, measured, ok):
        if oid not in goals:
            return

        o = goals[oid]
        out.append({"id": oid, "text": o["text"], "goal": goal_text(o), "measured": measured if measured is not None else "n/a", "verdict": "not measured" if ok is None else ("met" if ok else "NOT met")})

    # A goal that is a time for every tier: all tiers that were drilled must reach it; a tier that never got there counts as NOT met.
    # When fewer than five tiers were drilled, a result that is good so far is "not measured": the other tiers could still fail.
    def every_tier(oid, key):
        if oid not in goals:
            return

        o = goals[oid]

        if not tiers:
            add(oid, "no tier was drilled", None)
            return

        parts, bad = [], []

        for t in tiers:
            value = key(t)

            if value is None:
                parts.append(t["tier"] + ": never within the time limit")
                bad.append(t["tier"])
            else:
                parts.append(t["tier"] + " " + seconds(value))

                if not meets(o, value):
                    bad.append(t["tier"])

        if bad:
            add(oid, ", ".join(parts), False)
        elif len(tiers) < len(ORDER):
            add(oid, ", ".join(parts) + " (only " + str(len(tiers)) + " of " + str(len(ORDER)) + " tiers were drilled)", None)
        else:
            add(oid, ", ".join(parts), True)

    every_tier("pod-back", lambda t: t.get("pod_back_s"))
    every_tier("whole-again", lambda t: t.get("whole_s"))
    every_tier("event-flows", lambda t: (t.get("event") or {}).get("readable_s"))

    if "data-kept" in goals:
        o = goals["data-kept"]

        if not tiers:
            add("data-kept", "no tier was drilled", None)
        else:
            kept = [t for t in tiers if (t.get("data") or {}).get("kept") is True]
            lost = [t for t in tiers if (t.get("data") or {}).get("kept") is False]
            unknown = [t for t in tiers if (t.get("data") or {}).get("kept") is None]
            percent = 100.0 * len(kept) / len(tiers)
            # The most the run could still reach if every tier that is not drilled (or not checked) kept its data.
            total = max(len(tiers), len(ORDER))
            best = 100.0 * (total - len(lost)) / total
            text = format(percent, ".0f") + " percent (" + str(len(kept)) + " of " + str(len(tiers)) + " tiers kept all their data)" + (". Data missing after: " + ", ".join(t["tier"] for t in lost) if lost else "")

            if lost and not meets(o, best):
                add("data-kept", text, False)
            elif unknown:
                add("data-kept", text + ". Not checked: " + ", ".join(t["tier"] for t in unknown), None)
            elif len(tiers) < len(ORDER):
                add("data-kept", text + " (only " + str(len(tiers)) + " of " + str(len(ORDER)) + " tiers were drilled)", None)
            else:
                add("data-kept", text, meets(o, percent))

    if "api-gap" in goals:
        o = goals["api-gap"]
        rest = next((t for t in tiers if t["tier"] == "restapi"), None)

        if rest is None:
            add("api-gap", "the REST API tier was not drilled", None)
        elif (rest.get("rest") or {}).get("stats_gap_s") is None:
            add("api-gap", "the REST API was not asked during the drill", None)
        else:
            r = rest["rest"]
            add("api-gap", "longest stretch of failed /stats calls: " + format(r["stats_gap_s"], ".1f") + " s (" + str(r["stats_failed"]) + " of " + str(r["calls"]) + " calls failed)", meets(o, r["stats_gap_s"]))

    return out


def goals_lines(summary):
    verdicts = judge(summary)
    lines = ["## The goals (service level objectives)", "",
             "The goals are the numbers in `slo.json` whose field `test` says `drill`. Each line says what was asked, what this run measured, and whether the goal was met. "
             "Change a goal with `make goals` and run `make drill-report`: the verdicts change, the measurements do not.", ""]

    if not verdicts:
        lines.append("`slo.json` has no goal that the drill can measure.")
        return lines, verdicts

    lines.append("| Goal | Asked | This run | |")
    lines.append("|---|---|---|---|")

    for v in verdicts:
        lines.append("| " + v["text"] + " | " + v["goal"] + " | " + v["measured"] + " | **" + v["verdict"] + "** |")

    return lines, verdicts


# ---------------------------------------------------------------- the table

def local_time(stamp):
    if not stamp:
        return "?"

    try:
        when = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return stamp

    return when.strftime("%H:%M:%S") + " UTC (" + when.astimezone().strftime("%H:%M:%S") + " on the computer that ran it)"


def data_word(t):
    kept = (t.get("data") or {}).get("kept")
    return "kept" if kept is True else ("LOST" if kept is False else "not checked")


def rest_word(t):
    r = t.get("rest") or {}

    if not r.get("calls"):
        return "n/a"

    return str(r.get("stats_failed", 0)) + " of " + str(r["calls"]) + (" (" + format(r["stats_gap_s"], ".0f") + " s)" if r.get("stats_failed") else "")


def results_table(summary, goal_lines):
    tiers = summary.get("tiers", [])
    out = ["# Resilience drill: one pod deleted in every tier", ""]
    out.append("Run at " + str(summary.get("started", "?")) + ", " + str(len(drilled(summary))) + " of " + str(len(summary.get("planned") or ORDER)) + " tiers drilled. "
               "Every number is in seconds after the pod was deleted (`kubectl delete pod`).")

    if summary.get("aborted"):
        out += ["", "**The drill stopped early:** " + summary["aborted"]]

    if summary.get("load_left"):
        out += ["", "**Warning:** the load generator may still be running on the cluster. Run `make scale-down`."]

    out += ["", "## What happened, tier by tier", "",
            "| Tier | Pod deleted | New pod ready | Whole stack | Event readable | REST `/stats` calls failed | Data | Result |", "|---|---|---|---|---|---|---|---|"]

    for t in tiers:
        if not t.get("deleted"):
            out.append("| " + name_of(t["tier"]) + " | (not deleted) | | | | | | **not run:** " + str(t.get("problem") or "?").splitlines()[0][:100] + " |")
            continue

        out.append("| " + name_of(t["tier"]) + " | `" + t["victim"] + "` | " + seconds(t.get("pod_back_s")) + " | " + seconds(t.get("whole_s")) + " | " + seconds((t.get("event") or {}).get("readable_s"))
                   + " | " + rest_word(t) + " | " + data_word(t) + " | **" + ("PASS" if t.get("passed") else "FAIL") + "** |")

    out += ["", "*New pod ready*: the replacement pod reports Ready. *Whole stack*: every part (including Prometheus, Grafana, Loki and the exporters) has its pods ready again. "
            "*Event readable*: a tagged event sent right after the delete can be read from the REST API. "
            "*REST `/stats` calls failed*: the drill asked the REST API for `/stats` about once a second from the computer that ran it; in brackets the longest stretch of failures."]

    for t in tiers:
        if not t.get("deleted"):
            continue

        out += ["", "### " + name_of(t["tier"]), "",
                "- Pod deleted: `" + t["victim"] + "` (" + str(t.get("kind", "?")) + ") at " + local_time(t.get("delete_wall")),
                "- Replacement: " + (("`" + str(t["replacement"]) + "`") if t.get("replacement") else "no replacement pod was ready") + ("; the old pod was gone after " + seconds(t["old_gone_s"]) if t.get("old_gone_s") is not None else ""),
                "- " + (("New pod ready after " + seconds(t["pod_back_s"])) if t.get("pod_back_s") is not None else "New pod ready: never within the time limit")
                + ((", whole stack after " + seconds(t["whole_s"])) if t.get("whole_s") is not None else ", whole stack: never within the time limit")
                + ("; the producer was sending again after " + seconds(t["sending_again_s"]) if t.get("sending_again_s") is not None else "")]
        e = t.get("event") or {}

        if e.get("trace"):
            out.append("- Test event `" + e["trace"] + "`: " + ("Kafka stored it " + seconds(e["sent_s"]) + " after the delete" if e.get("sent_s") is not None else "not sent") + "; "
                       + ("readable from the REST API after " + seconds(e["readable_s"]) if e.get("readable_s") is not None else "never readable within the time limit")
                       + ("; stored " + str(e["copies"]) + " time" + ("" if e["copies"] == 1 else "s") if e.get("copies") is not None else "") + (". " + str(e["error"]) if e.get("error") else ""))

        for c in (t.get("data") or {}).get("checks", []):
            out.append("- " + ("OK" if c.get("ok") is True else ("PROBLEM" if c.get("ok") is False else "not checked")) + ": " + c["what"] + ": " + str(c.get("before", "")) + " -> " + str(c.get("after", ""))
                       + ((" (" + c["note"] + ")") if c.get("note") else ""))

        if t.get("restarts"):
            out.append("- Containers that Kubernetes restarted by itself meanwhile: " + ", ".join(k + " (+" + str(v) + ")" for k, v in sorted(t["restarts"].items())))

        r = t.get("rest") or {}

        if r.get("calls"):
            out.append("- REST API, asked from outside every second: `/health` failed " + str(r.get("health_failed", 0)) + " of " + str(r["calls"]) + " times (longest stretch " + format(r.get("health_gap_s", 0), ".1f")
                       + " s), `/stats` failed " + str(r.get("stats_failed", 0)) + " of " + str(r["calls"]) + " times (longest stretch " + format(r.get("stats_gap_s", 0), ".1f") + " s)")

        if t.get("problem"):
            out.append("- **Problem:** " + " ".join(str(t["problem"]).split()))

    out += [""] + goal_lines + [""]

    if summary.get("windows"):
        out += ["## Where to look in Grafana and Loki", "",
                "Open the dashboard CA3 pipeline and set the time range to the minutes below (the clock of the computer that ran the drill):", ""]

        for w in summary["windows"]:
            out.append("- " + name_of(w["tier"]) + ": from " + w["from_local"] + " to " + w["to_local"])

    return "\n".join(out) + "\n"


# ---------------------------------------------------------------- the chart

COLORS = {"gone": "#c62828", "pod": "#1f77b4", "whole": "#2ca02c", "event": "#7b3294", "rest": "#d95f02"}


# One row per tier. The bar runs from the delete (0 s) to the moment the whole stack was whole again; marks show the new pod ready and the test event readable;
# the orange stripe is the stretch in which the REST API did not answer /stats from outside (when the drill saw one).
def chart(summary):
    tiers = drilled(summary)

    if not tiers:
        return None

    latest = max([x for t in tiers for x in (t.get("pod_back_s"), t.get("whole_s"), (t.get("event") or {}).get("readable_s"), 10) if x is not None])
    step = 10 if latest <= 50 else (20 if latest <= 100 else (30 if latest <= 180 else (60 if latest <= 480 else 120)))
    top = (int(latest / step) + 1) * step
    left, right, row, head = 120, 30, 46, 54
    width = 880
    plot = width - left - right
    items = (("pod", "new pod ready"), ("whole", "whole stack again (green, lighter)"), ("event", "test event readable from the REST API"), ("rest", "REST API did not answer /stats"))

    # The legend goes on as many lines as it needs (each item is about 6 pixels per letter wide); the picture is as high as the lines need.
    places, cursor, line = [], left, 0

    for key, label in items:
        size = 24 + int(len(label) * 6.4) + 14

        if cursor + size > width - 20 and cursor > left:
            cursor, line = left, line + 1

        places.append((key, label, cursor, line))
        cursor += size

    height = head + row * len(tiers) + 36 + 18 * (line + 1)
    x = lambda s: left + plot * s / float(top)
    svg = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ' + str(width) + " " + str(height) + '" font-family="Helvetica, Arial, sans-serif" font-size="12">',
           '<rect width="100%" height="100%" fill="#ffffff"/>',
           '<text x="' + str(left) + '" y="22" font-size="15" font-weight="bold" fill="#222">Resilience drill: seconds after the pod was deleted</text>']

    for s in range(0, top + 1, step):
        svg.append('<line x1="' + format(x(s), ".1f") + '" y1="' + str(head - 8) + '" x2="' + format(x(s), ".1f") + '" y2="' + str(head + row * len(tiers)) + '" stroke="#ddd"/>')
        svg.append('<text x="' + format(x(s), ".1f") + '" y="' + str(head - 14) + '" text-anchor="middle" fill="#555">' + str(s) + ' s</text>')

    for i, t in enumerate(tiers):
        y = head + i * row
        mid = y + row / 2.0
        end = t.get("whole_s") if t.get("whole_s") is not None else (t.get("pod_back_s") if t.get("pod_back_s") is not None else top)
        svg.append('<text x="' + str(left - 10) + '" y="' + format(mid + 4, ".1f") + '" text-anchor="end" fill="#222" font-weight="bold">' + escape(name_of(t["tier"])) + '</text>')
        svg.append('<rect x="' + format(x(0), ".1f") + '" y="' + format(mid - 9, ".1f") + '" width="' + format(max(2.0, x(end) - x(0)), ".1f") + '" height="18" rx="3" fill="' + (COLORS["whole"] if t.get("whole_s") is not None else COLORS["gone"]) + '" opacity="0.35"/>')

        if t.get("pod_back_s") is not None:
            svg.append('<rect x="' + format(x(0), ".1f") + '" y="' + format(mid - 9, ".1f") + '" width="' + format(max(2.0, x(t["pod_back_s"]) - x(0)), ".1f") + '" height="18" rx="3" fill="' + COLORS["pod"] + '" opacity="0.8"/>')

        r = t.get("rest") or {}

        if r.get("stats_gap_s") and r.get("stats_gap_start_s") is not None:
            svg.append('<rect x="' + format(x(r["stats_gap_start_s"]), ".1f") + '" y="' + format(mid + 11, ".1f") + '" width="' + format(max(2.0, x(r["stats_gap_start_s"] + r["stats_gap_s"]) - x(r["stats_gap_start_s"])), ".1f")
                       + '" height="5" fill="' + COLORS["rest"] + '"/>')

        ready = (t.get("event") or {}).get("readable_s")

        if ready is not None:
            svg.append('<circle cx="' + format(x(ready), ".1f") + '" cy="' + format(mid, ".1f") + '" r="6" fill="' + COLORS["event"] + '" stroke="#fff" stroke-width="1.5"/>')

        if t.get("pod_back_s") is not None:
            svg.append('<text x="' + format(x(t["pod_back_s"]) + 4, ".1f") + '" y="' + format(mid - 12, ".1f") + '" fill="#145a8d" font-size="11">' + format(t["pod_back_s"], ".0f") + ' s</text>')

    legend = head + row * len(tiers) + 26

    for key, label, cursor, line in places:
        top_y = legend + 18 * line
        svg.append('<rect x="' + str(cursor) + '" y="' + str(top_y) + '" width="14" height="10" fill="' + COLORS[key] + '"/>')
        svg.append('<text x="' + str(cursor + 19) + '" y="' + str(top_y + 10) + '" fill="#333" font-size="11">' + escape(label) + '</text>')

    svg.append("</svg>")

    return "\n".join(svg) + "\n"


# ---------------------------------------------------------------- writing the files

def render(directory, prefix):
    summary = load(directory, prefix)
    lines, verdicts = goals_lines(summary)
    text = results_table(summary, lines)
    (Path(directory) / (prefix + "-results.md")).write_text(text)
    (Path(directory) / (prefix + "-goals.json")).write_text(json.dumps(verdicts, indent=2) + "\n")
    drawing = chart(summary)

    if drawing:
        (Path(directory) / (prefix + "-chart.svg")).write_text(drawing)

    return text


# One line with the verdicts of the goals ("3 met, 1 NOT met, 1 not measured"), read from the file that render() wrote.
def goal_tally(directory, prefix):
    try:
        verdicts = json.loads((Path(directory) / (prefix + "-goals.json")).read_text())
    except (OSError, ValueError):
        return None

    if not verdicts:
        return None

    count = lambda word: sum(1 for v in verdicts if v["verdict"] == word)

    return "Goals in slo.json: " + str(count("met")) + " met, " + str(count("NOT met")) + " NOT met, " + str(count("not measured")) + " not measured (" + str(len(verdicts)) + " goals)."


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Draw the table, the goals and the chart of a saved drill again.")
    parser.add_argument("--out", "--dir", dest="out", default="evidence", help="the folder with the saved files (default: evidence)")
    parser.add_argument("--prefix", default="step7-drill", help="the start of the file names (default: step7-drill)")
    args, _ = parser.parse_known_args()

    try:
        print(render(args.out, args.prefix))
    except FileNotFoundError as problem:
        sys.exit("Cannot read " + str(problem.filename) + ". Run  make drill  first, or give the folder with --out.")

    wrote = [args.prefix + s for s in ("-results.md", "-goals.json", "-chart.svg") if (Path(args.out) / (args.prefix + s)).exists()]
    print("Wrote " + ", ".join(wrote) + " in " + args.out + ("" if len(wrote) == 3 else "  (no chart: no pod was deleted in that drill)"))
    print(goal_tally(args.out, args.prefix) or "")
