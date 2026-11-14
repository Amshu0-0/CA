# The numbers that describe a replay: for each detector, how its verdicts on the windows compare with the ground truth (the BGL alert tags).
# No dependencies, so the REST API (restapi/rest_app.py) and the local replay (scripts/local-replay.py) calculate it the same way.
#
# A window is "truth" if it holds at least one line with an alert tag; a detector flags a window if its verdict (0 or 1) is 1. For the LLM
# a template without a saved decision counts as "not flagged" (the saved experiment covers the templates of its validation and test
# periods, not every template that exists).

# (field in the window documents, name shown to people). The letters are the conditions of the NLP project's report.
DETECTORS = (
    ("logbert", "LogBERT, full coverage (A+)"),
    ("llm", "rule-guided LLM, saved decisions (B3)"),
    ("keyword", "keyword rules (B4)"),
)


# Precision, recall, F1 and the false-positive rate from the four counts of a confusion matrix.
def rates(tp, fp, tn, fn):
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    false_positive_rate = fp / (fp + tn) if fp + tn else 0.0

    return {"tp": tp, "fp": fp, "tn": tn, "fn": fn, "precision": round(precision, 4), "recall": round(recall, 4), "f1": round(f1, 4),
            "false_positive_rate": round(false_positive_rate, 4)}


# The four counts of one detector against the truth, for window documents (dictionaries with "truth" and the detector's own field).
def confusion(windows, field):
    tp = fp = tn = fn = 0

    for window in windows:
        truth, said = window["truth"], window.get(field) or 0

        if truth and said:
            tp += 1
        elif said:
            fp += 1
        elif truth:
            fn += 1
        else:
            tn += 1

    return rates(tp, fp, tn, fn)


# The value below which `q` (0 to 1) of the sorted numbers lie (nearest rank).
def percentile(values, q):
    if not values:
        return 0.0

    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, int(round(q * len(ordered) + 0.5)) - 1))]


# Everything the summary says about the windows of one run.
def summarize(windows):
    windows = list(windows)
    seconds = [w.get("score_ms", 0.0) for w in windows if w.get("score_ms") is not None]

    return {
        "windows": len(windows),
        "anomalous_windows": sum(1 for w in windows if w["truth"]),
        "detectors": {field: {"name": name, **confusion(windows, field)} for field, name in DETECTORS},
        "logbert_ms_per_window": {"mean": round(sum(seconds) / len(seconds), 1) if seconds else 0.0, "p95": round(percentile(seconds, 0.95), 1),
                                  "max": round(max(seconds), 1) if seconds else 0.0},
    }
