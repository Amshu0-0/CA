# The text of the result tables, shared by scripts/local-replay.py (your computer) and scripts/results.py (the cluster), so that both print
# the same table from the same kind of dictionary: the answer of the REST API's /summary (restapi/summary.py describes the numbers).

import csv
from pathlib import Path

# The detectors, in the order of the table. The names are the ones restapi/summary.py gives them.
ORDER = ("logbert", "llm", "keyword")


def percent(value):
    return f"{100 * value:5.1f}%"


# The lines of the table for one /summary answer.
def detector_table(summary):
    rows = [f"  {'detector':<40} {'flagged':>8} {'TP':>6} {'FP':>6} {'TN':>6} {'FN':>6} {'precision':>10} {'recall':>8} {'F1':>7} {'FP rate':>13}"]

    for field in ORDER:
        d = summary["detectors"][field]
        rows.append(f"  {d['name']:<40} {d['tp'] + d['fp']:>8} {d['tp']:>6} {d['fp']:>6} {d['tn']:>6} {d['fn']:>6} {d['precision']:>10.3f} {d['recall']:>8.3f} {d['f1']:>7.3f} {percent(d['false_positive_rate']):>13}")

    return rows


# Everything one summary says, as lines of text.
def describe(summary):
    run, lines, speed = summary["run"], summary["lines"], summary["logbert_ms_per_window"]
    unseen, nodecision = lines["template_not_known"], lines["llm_has_no_saved_decision"]
    out = [
        f"Replay {run['run']}   status: {run.get('status', '?')}",
        f"  log lines: {lines['stored']}, of which with an alert tag (the ground truth): {lines['with_alert_tag']}",
        f"  windows:   {summary['windows']}, of which anomalous (they hold at least one alert line): {summary['anomalous_windows']}",
        f"  lines whose template Drain does not know: {unseen}.  Lines for which the LLM has no saved decision (counted as not flagged): {nodecision}",
        "",
        *detector_table(summary),
        "",
        f"  LogBERT needed per window: {speed['mean']} ms on average, 95% of the windows under {speed['p95']} ms, the slowest {speed['max']} ms",
    ]

    return out


# The numbers of the NLP project's own evaluation of the same three detectors on its WHOLE test period, from results/comparison.csv.
# The replay file is a slice of that period, so these are a reference and not a target.
def reference(csv_path):
    path = Path(csv_path)

    if not path.is_file():
        return []

    wanted = {"logbert_full": "LogBERT, full coverage (A+)", "llm_rule_based": "rule-guided LLM (B3)", "keyword_rules": "keyword rules (B4)"}
    rows = {r["method"]: r for r in csv.DictReader(path.open(newline="", encoding="utf-8"))}

    if not all(key in rows for key in wanted):
        return []

    first = rows["logbert_full"]
    out = [f"For comparison, the NLP project's report on its whole test period ({first['support']} windows; results/comparison.csv). The replay file is a slice of that period, so the numbers differ:"]

    for key, name in wanted.items():
        r = rows[key]
        out.append(f"  {name:<40} precision {float(r['precision']):.3f}   recall {float(r['recall']):.3f}   F1 {float(r['f1']):.3f}")

    return out


# What differs between two summaries of the same replay (for example the laptop's and the cluster's): a list of sentences, empty if nothing.
def differences(a, b, names=("first", "second")):
    found = []

    for key in ("windows", "anomalous_windows"):
        if a[key] != b[key]:
            found.append(f"{key}: {names[0]} {a[key]}, {names[1]} {b[key]}")

    for key in ("stored", "with_alert_tag", "template_not_known", "llm_has_no_saved_decision"):
        if a["lines"][key] != b["lines"][key]:
            found.append(f"lines.{key}: {names[0]} {a['lines'][key]}, {names[1]} {b['lines'][key]}")

    for field in ORDER:
        for count in ("tp", "fp", "tn", "fn"):
            x, y = a["detectors"][field][count], b["detectors"][field][count]

            if x != y:
                found.append(f"{a['detectors'][field]['name']}: {count} {names[0]} {x}, {names[1]} {y}")

    return found
