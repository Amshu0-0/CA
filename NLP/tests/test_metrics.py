"""Tests for the shared evaluation metrics."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from logad.evaluation.metrics import binary_metrics, metrics_table, windows_from_message_flags


# Counts and derived metrics on a hand-checked example.
def test_binary_metrics() -> None:
    m = binary_metrics([1, 1, 1, 0, 0, 0, 0], [1, 1, 0, 1, 0, 0, 0])
    assert (m.tp, m.fp, m.tn, m.fn) == (2, 1, 3, 1)
    assert m.precision == pytest.approx(2 / 3)
    assert m.recall == pytest.approx(2 / 3)
    assert m.f1 == pytest.approx(2 / 3)
    assert m.false_positive_rate == pytest.approx(1 / 4)
    assert m.support == 7


# Degenerate cases return 0 instead of dividing by zero.
def test_metrics_without_positives() -> None:
    m = binary_metrics([0, 0], [0, 0])
    assert m.precision == m.recall == m.f1 == 0.0
    assert m.as_dict()["tn"] == 2


# Mismatched lengths are rejected.
def test_shape_mismatch() -> None:
    with pytest.raises(ValueError):
        binary_metrics([0, 1], [0])


# A window is flagged if any of its messages is flagged.
def test_windows_from_message_flags() -> None:
    flags = np.array([0, 0, 1, 0, 0])
    windows = pd.DataFrame({"start_idx": [0, 0, 3, 2], "end_idx": [2, 3, 5, 3]})
    assert windows_from_message_flags(flags, windows).tolist() == [0, 1, 0, 1]


# The Markdown table contains one row per method with formatted numbers.
def test_metrics_table() -> None:
    table = metrics_table(
        {"LogBERT": {"precision": 0.5, "recall": 1.0, "f1": 2 / 3, "fp": 3, "seconds": 1.234}},
        extra_columns={"seconds": "Time (s)"},
    )
    lines = table.splitlines()
    assert lines[0] == "| Method | Precision | Recall | F1 | FP | Time (s) |"
    assert lines[2] == "| LogBERT | 0.500 | 1.000 | 0.667 | 3 | 1.23 |"
