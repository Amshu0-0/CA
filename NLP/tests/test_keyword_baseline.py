"""Tests for the keyword baseline and template-level evaluation."""

from __future__ import annotations

from logad.evaluation.template_level import (
    decisions_frame,
    evaluate_template_labels,
    message_flags,
    templates_in_splits,
)
from logad.rules.keyword import KeywordClassifier
from logad.rules.rule import Rule

RULES = [
    Rule("interrupts are bad", ("interrupt",), 1),
    Rule("corrected errors are fine", ("corrected",), 0),
    Rule("TLB problems are bad", ("tlb",), 1),
]


# The first matching rule in rank order decides; no match -> normal.
def test_first_matching_rule_wins() -> None:
    classifier = KeywordClassifier(RULES)
    tlb = classifier.classify(1, "data TLB error interrupt")
    assert (tlb.label, tlb.rule, tlb.reason) == (1, 1, "interrupt")  # rule 1 before rule 3

    corrected = classifier.classify(2, "instruction cache parity error corrected")
    assert (corrected.label, corrected.rule) == (0, 2)

    unknown = classifier.classify(3, "generating core.<*>")
    assert (unknown.label, unknown.rule) == (0, None)


# Decisions become a tidy DataFrame.
def test_decisions_frame() -> None:
    frame = decisions_frame([KeywordClassifier(RULES).classify(7, "x interrupt")])
    assert frame.loc[0, "template_id"] == 7 and frame.loc[0, "label"] == 1


# On the synthetic log, labeling the alert template abnormal catches every
# anomalous validation window and message, with no false positives.
def test_evaluate_template_labels(processed_synthetic) -> None:
    _, logs = processed_synthetic
    text_to_id = {text: tid for tid, text in logs.template_text.items()}
    alert_id = text_to_id["data TLB error interrupt"]
    assert alert_id in templates_in_splits(logs, ["val"])
    assert alert_id not in templates_in_splits(logs, ["train"])

    decisions = {
        tid: KeywordClassifier(RULES).classify(tid, text).label
        for tid, text in logs.template_text.items()
    }
    flags = message_flags(logs, decisions)
    assert flags.sum() == logs.message_labels.sum() == 6

    result = evaluate_template_labels(logs, decisions, "val")
    assert result["window"]["f1"] == 1.0 and result["window"]["fp"] == 0
    assert result["message"]["tp"] == 6
    assert len(result["window_preds"]) == len(logs.windows_in("val"))
