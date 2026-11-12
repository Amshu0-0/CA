"""Error analysis for the report.

* ``template_purity``        how often messages sharing a template carry
                             different labels, and the best F1 any
                             template-level method could reach (an oracle that
                             labels each template by its test majority)
* ``per_category_recall``    recall of every method per BGL alert category
                             (KERNDTLB, APPREAD, ...)
* ``method_agreement``       which anomalous windows each pair of methods
                             catches together / alone / not at all
* ``missed_alert_templates`` the alert templates most often inside the
                             windows a method missed
* ``template_errors``        Approach B's misclassified templates
"""

from __future__ import annotations

from itertools import combinations
from typing import Any

import numpy as np
import pandas as pd

from logad.data.processed import ProcessedLogs
from logad.data.windows import count_in_windows
from logad.evaluation.metrics import binary_metrics, windows_from_message_flags


# Label purity of templates within one split, plus the template-level oracle.
#
# A template is "mixed" in the split if some, but not all, of its messages
# there are alerts. No method that assigns one label per template can be
# right about every message of a mixed template; the oracle (majority label
# per template) shows the resulting ceiling at message and window level.
def template_purity(
    logs: ProcessedLogs, split: str = "test"
) -> tuple[dict[str, Any], pd.DataFrame]:
    templates = logs.templates
    total, anomalous = templates[f"n_{split}"], templates[f"n_{split}_anomalous"]
    present = total > 0
    mixed = present & (anomalous > 0) & (anomalous < total)

    oracle = {
        int(t): int(2 * a > n)
        for t, a, n in zip(templates["template_id"], anomalous, total, strict=True)
        if n > 0
    }
    abnormal_ids = np.array([t for t, label in oracle.items() if label], dtype=np.int64)
    flags = np.isin(logs.template_ids, abnormal_ids).astype(np.int8)
    in_split = (logs.messages["split"].astype(str) == split).to_numpy()
    windows = logs.windows_in(split)
    oracle_windows = windows_from_message_flags(flags, windows)

    summary = {
        "split": split,
        "n_templates": int(present.sum()),
        "n_mixed_templates": int(mixed.sum()),
        "n_messages": int(total.sum()),
        "messages_in_mixed_templates": int(total[mixed].sum()),
        "anomalous_messages": int(anomalous.sum()),
        "anomalous_messages_in_mixed_templates": int(anomalous[mixed].sum()),
        "oracle_message_level": binary_metrics(
            logs.message_labels[in_split], flags[in_split]
        ).as_dict(),
        "oracle_window_level": binary_metrics(
            windows["label"].to_numpy(), oracle_windows
        ).as_dict(),
    }
    columns = ["template_id", "template", f"n_{split}", f"n_{split}_anomalous", "alert_tags"]
    mixed_table = templates.loc[mixed, columns].sort_values(f"n_{split}", ascending=False)
    return summary, mixed_table.reset_index(drop=True)


# For every alert category, the share of test windows containing it that
# each method flagged. `predictions` maps method -> 0/1 array aligned with
# logs.windows_in(split).
def per_category_recall(
    logs: ProcessedLogs, predictions: dict[str, np.ndarray], split: str = "test"
) -> pd.DataFrame:
    windows = logs.windows_in(split)
    pairs = windows[["start_idx", "end_idx"]].to_numpy()
    tags = logs.messages["alert_tag"].astype(str).to_numpy()
    rows = []
    for category in sorted(set(tags[logs.message_labels == 1])):
        contains = count_in_windows((tags == category).astype(np.int64), pairs) > 0
        if not contains.any():
            continue
        row = {"category": category, "n_windows": int(contains.sum())}
        for method, preds in predictions.items():
            row[method] = float(np.asarray(preds)[contains].mean())
        rows.append(row)
    return pd.DataFrame(rows).sort_values("n_windows", ascending=False).reset_index(drop=True)


# For every pair of methods, split the anomalous windows into caught by both,
# by only one of them, or by neither; also count shared false positives.
def method_agreement(labels: np.ndarray, predictions: dict[str, np.ndarray]) -> pd.DataFrame:
    labels = np.asarray(labels).astype(bool)
    rows = []
    for (name_a, a), (name_b, b) in combinations(predictions.items(), 2):
        a, b = np.asarray(a).astype(bool), np.asarray(b).astype(bool)
        rows.append(
            {
                "method_a": name_a,
                "method_b": name_b,
                "both_caught": int(np.sum(labels & a & b)),
                "only_a": int(np.sum(labels & a & ~b)),
                "only_b": int(np.sum(labels & ~a & b)),
                "neither": int(np.sum(labels & ~a & ~b)),
                "shared_false_positives": int(np.sum(~labels & a & b)),
            }
        )
    return pd.DataFrame(rows)


# The alert templates that occur most often inside the anomalous windows a
# method missed (false negatives), with the number of missed windows each
# appears in. Explains *what kind* of anomaly a method overlooks.
def missed_alert_templates(
    logs: ProcessedLogs, predictions: np.ndarray, split: str = "test", top: int = 10
) -> pd.DataFrame:
    windows = logs.windows_in(split)
    missed = windows[(windows["label"] == 1) & (np.asarray(predictions) == 0)]
    counts: dict[int, int] = {}
    for start, end in missed[["start_idx", "end_idx"]].to_numpy():
        alert_mask = logs.message_labels[start:end] == 1
        for template_id in np.unique(logs.template_ids[start:end][alert_mask]):
            counts[int(template_id)] = counts.get(int(template_id), 0) + 1
    rows = [
        {"template_id": t, "template": logs.template_text[t], "missed_windows": n}
        for t, n in sorted(counts.items(), key=lambda item: -item[1])[:top]
    ]
    return pd.DataFrame(rows, columns=["template_id", "template", "missed_windows"])


# Approach B's template-level mistakes from a decisions table (written by
# scripts/run_rule_guided.py): false negatives are templates with test alerts
# labeled normal; false positives are templates without test alerts labeled
# abnormal. Sorted by the number of test messages affected.
def template_errors(decisions: pd.DataFrame) -> pd.DataFrame:
    in_test = decisions[decisions["n_test"] > 0].copy()
    has_alerts = in_test["n_test_anomalous"] > 0
    in_test["error"] = np.select(
        [has_alerts & (in_test["label"] == 0), ~has_alerts & (in_test["label"] == 1)],
        ["false negative", "false positive"],
        default="",
    )
    errors = in_test[in_test["error"] != ""]
    columns = ["error", "template_id", "template", "n_test", "n_test_anomalous", "rule", "reason"]
    return errors.sort_values("n_test", ascending=False)[columns].reset_index(drop=True)
