"""Error analysis for the report.

* ``per_category_recall``        recall of every LogBERT variant per BGL alert
                                 category (KERNDTLB, APPREAD, ...)
* ``method_agreement``           which anomalous windows two variants catch
                                 together / alone / not at all
* ``missed_alert_templates``     the alert templates most often inside the
                                 windows a variant missed, i.e. what kind of
                                 anomaly LogBERT overlooks
"""

from __future__ import annotations

from itertools import combinations

import numpy as np
import pandas as pd

from logad.data.processed import ProcessedLogs
from logad.data.windows import count_in_windows


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


# For every pair of variants, split the anomalous windows into caught by both,
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


# The alert templates inside the windows selected by `mask` (a boolean array
# aligned with logs.windows_in(split)), each with the number of selected
# windows it occurs in; the `top` most frequent are returned.
def alert_templates_in_windows(
    logs: ProcessedLogs, mask: np.ndarray, split: str = "test", top: int = 10
) -> pd.DataFrame:
    windows = logs.windows_in(split)
    selected = windows[np.asarray(mask, dtype=bool)]
    counts: dict[int, int] = {}
    for start, end in selected[["start_idx", "end_idx"]].to_numpy():
        alert_mask = logs.message_labels[start:end] == 1
        for template_id in np.unique(logs.template_ids[start:end][alert_mask]):
            counts[int(template_id)] = counts.get(int(template_id), 0) + 1
    rows = [
        {"template_id": t, "template": logs.template_text[t], "windows": n}
        for t, n in sorted(counts.items(), key=lambda item: -item[1])[:top]
    ]
    return pd.DataFrame(rows, columns=["template_id", "template", "windows"])


# The alert templates that occur most often inside the anomalous windows a
# method missed (false negatives), with the number of missed windows each
# appears in. Explains *what kind* of anomaly a method overlooks.
def missed_alert_templates(
    logs: ProcessedLogs, predictions: np.ndarray, split: str = "test", top: int = 10
) -> pd.DataFrame:
    windows = logs.windows_in(split)
    missed = (windows["label"].to_numpy() == 1) & (np.asarray(predictions) == 0)
    table = alert_templates_in_windows(logs, missed, split, top)
    return table.rename(columns={"windows": "missed_windows"})
