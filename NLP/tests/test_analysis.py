"""Tests for the error-analysis helpers."""

from __future__ import annotations

import numpy as np

from logad.evaluation.analysis import (
    method_agreement,
    missed_alert_templates,
    per_category_recall,
)


# Per-category recall is the share of windows with that category a method flagged.
def test_per_category_recall(processed_synthetic) -> None:
    _, logs = processed_synthetic
    windows = logs.windows_in("val")
    perfect = windows["label"].to_numpy()
    table = per_category_recall(logs, {"perfect": perfect, "none": np.zeros_like(perfect)}, "val")
    assert table["category"].tolist() == ["KERNDTLB"]
    assert table.loc[0, "perfect"] == 1.0 and table.loc[0, "none"] == 0.0


# Agreement counts partition the anomalous windows for every method pair.
def test_method_agreement() -> None:
    labels = np.array([1, 1, 1, 1, 0])
    table = method_agreement(labels, {"a": [1, 1, 0, 0, 1], "b": [1, 0, 1, 0, 1]})
    row = table.iloc[0]
    assert (row.both_caught, row.only_a, row.only_b, row.neither) == (1, 1, 1, 1)
    assert row.shared_false_positives == 1


# Missed windows are traced back to the alert templates inside them.
def test_missed_alert_templates(processed_synthetic) -> None:
    _, logs = processed_synthetic
    windows = logs.windows_in("val")
    missed = missed_alert_templates(logs, np.zeros(len(windows), dtype=int), "val")
    assert missed["template"].tolist() == ["data TLB error interrupt"]
    assert missed.loc[0, "missed_windows"] == int(windows["label"].sum())
