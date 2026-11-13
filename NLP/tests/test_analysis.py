"""Tests for the error-analysis helpers."""

from __future__ import annotations

import numpy as np
import pandas as pd

from logad.evaluation.analysis import (
    combination_metrics,
    exclusive_alert_templates,
    keyword_rule_sensitivity,
    label_drift,
    method_agreement,
    missed_alert_templates,
    per_category_recall,
    rule_faithfulness,
    template_errors,
    template_purity,
)
from logad.rules.rule import Rule


# The synthetic alert template is pure, so the oracle is perfect on "val".
def test_template_purity(processed_synthetic) -> None:
    _, logs = processed_synthetic
    summary, mixed = template_purity(logs, "val")
    assert summary["n_mixed_templates"] == 0 and mixed.empty
    assert summary["anomalous_messages"] == 6
    assert summary["oracle_message_level"]["f1"] == 1.0
    assert summary["oracle_window_level"]["f1"] == 1.0


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


# Template-level errors are split into false negatives and false positives.
def test_template_errors() -> None:
    decisions = pd.DataFrame(
        {
            "template_id": [1, 2, 3, 4],
            "template": ["a", "b", "c", "d"],
            "label": [0, 1, 1, 0],
            "n_test": [10, 5, 0, 7],
            "n_test_anomalous": [3, 0, 0, 0],
            "rule": [None, 2, None, None],
            "reason": ["", "", "", ""],
        }
    )
    errors = template_errors(decisions)
    assert errors[["error", "template_id"]].values.tolist() == [
        ["false negative", 1],
        ["false positive", 2],
    ]


# A template is drifted if its majority label differs between training and test.
def test_label_drift() -> None:
    templates = pd.DataFrame(
        {
            "template_id": [1, 2, 3, 4],
            "template": ["login failed", "socket closed", "core dumped", "new message"],
            "n_train": [10, 5, 8, 0],
            "n_train_anomalous": [0, 5, 0, 0],
            "n_test": [4, 2, 3, 6],
            "n_test_anomalous": [4, 2, 0, 6],
        }
    )
    drifted = label_drift(templates)
    # Template 4 never occurs in training, so it cannot have drifted.
    assert drifted["template_id"].tolist() == [1]


# OR can only add detections and AND can only remove them.
def test_combination_metrics() -> None:
    labels = np.array([1, 1, 0, 0])
    table = combination_metrics(labels, {"a": [1, 0, 1, 0], "b": [0, 1, 0, 0]}, [("a", "b")])
    either, both = table.iloc[0], table.iloc[1]
    assert either.combination == "either (OR)" and (either.recall, either.fp) == (1.0, 1)
    assert both.combination == "both (AND)" and (both.recall, both.fp, both.f1) == (0.0, 0, 0.0)


# Windows caught by one method only are traced back to their alert templates.
def test_exclusive_alert_templates(processed_synthetic) -> None:
    _, logs = processed_synthetic
    labels = logs.windows_in("val")["label"].to_numpy()
    table = exclusive_alert_templates(logs, labels, np.zeros_like(labels), ("A", "B"), "val")
    assert table["caught_by"].tolist() == ["only A"]
    assert table.loc[0, "template"] == "data TLB error interrupt"
    assert table.loc[0, "windows"] == table.loc[0, "group_windows"] == int(labels.sum())


# A cited rule only counts as applied if one of its keywords occurs in the template.
def test_rule_faithfulness() -> None:
    rules = [
        Rule("Disk failures are abnormal", ("disk failure",), 1),
        Rule("Job starts are normal", ("started",), 0),
    ]
    decisions = pd.DataFrame(
        {
            "template_id": [1, 2, 3, 4, 5],
            "template": [
                "disk failure on <*>",
                "job <*> started",
                "ciod: error loading <*>",
                "kernel panic",
                "message only seen in validation",
            ],
            "label": [1, 0, 1, 1, 0],
            "rule": ["1.0", "2", "1.0", "", "2"],  # as read with keep_default_na=False
            "reason": ["", "", "", "", ""],
            "n_test": [5, 5, 5, 5, 0],
            "n_test_anomalous": [5, 0, 0, 2, 0],
        }
    )
    summary, table = rule_faithfulness(decisions, rules)
    assert table["template_id"].tolist() == [1, 2, 3, 4]  # template 5 is not in test
    assert summary["answers_citing_a_rule"] == 3
    assert summary["cited_rule_occurs_in_template"] == 2  # rule 1 is absent from template 3
    assert summary["cited_rule_is_keyword_rule"] == 2
    assert summary["abnormal_answers_backed_by_an_occurring_rule"] == 1
    assert summary["abnormal_where_keywords_say_normal"] == 2  # templates 3 and 4
    assert summary["abnormal_where_keywords_say_normal_with_alerts"] == 1  # template 4
    assert summary["normal_where_keywords_say_abnormal"] == 0
    assert table["cited_rule"].tolist()[:3] == [1, 2, 1]


# With only the normal rule nothing is flagged; adding the alert rule catches every alert window.
def test_keyword_rule_sensitivity(processed_synthetic) -> None:
    _, logs = processed_synthetic
    rules = [
        Rule("Alignment exceptions are normal", ("alignment exceptions",), 0),
        Rule("TLB errors are abnormal", ("data tlb error",), 1),
    ]
    table = keyword_rule_sensitivity(logs, rules, ks=[1, 2, 5], splits=["val", "test"])
    assert table["k"].tolist() == [1, 2]  # k is capped at the number of rules
    assert table.loc[0, "val_recall"] == 0.0
    assert (table.loc[1, "val_precision"], table.loc[1, "val_recall"]) == (1.0, 1.0)
    assert table.loc[1, "test_fp"] == 0
