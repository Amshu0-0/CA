"""Integration tests for running Approach B conditions on processed data.

Stub classifiers stand in for the LLM; they test the orchestration only.
"""

from __future__ import annotations

from logad.evaluation.template_level import TemplateDecision
from logad.rules.deduction import classify_templates, decisions_table, run_condition
from logad.rules.keyword import KeywordClassifier
from logad.rules.rule import Rule


# Stub "LLM" that labels by keyword and pretends some answers were cached.
class StubLLMClassifier:
    def classify(self, template_id: int, text: str) -> TemplateDecision:
        cached = template_id % 2 == 0
        return TemplateDecision(
            template_id,
            label=int("interrupt" in text),
            reason="stub",
            parsed=template_id != 1,  # pretend template 1 got an unusable answer
            latency_seconds=2.0 if cached else 0.0,
            cached=cached,
        )


# Cached answers keep their original latency; fresh ones are timed.
def test_classify_templates_timing() -> None:
    decisions = classify_templates(StubLLMClassifier(), [1, 2], {1: "a", 2: "b"})
    assert decisions[1].latency_seconds == 2.0  # cached -> original latency
    assert 0.0 <= decisions[0].latency_seconds < 1.0  # measured wall time


# The keyword condition on the synthetic log: every template in val/test is
# classified once, and the alert template catches all anomalous windows.
def test_run_condition_keyword(processed_synthetic) -> None:
    _, logs = processed_synthetic
    classifier = KeywordClassifier([Rule("TLB", ("tlb error",), 1)])
    result = run_condition(logs, classifier, "keyword_rules", ["val", "test"])

    classified = {d.template_id for d in result.decisions}
    expected = set(logs.template_ids[logs.messages["split"].astype(str).isin(["val", "test"])])
    assert classified == expected
    assert result.evaluations["val"]["window"]["recall"] == 1.0
    assert result.evaluations["val"]["window"]["fp"] == 0
    assert result.evaluations["test"]["window"]["fp"] == 0
    assert set(result.inference_seconds) == {"val", "test"}

    table = decisions_table(result, logs.templates)
    assert {"template", "n_test", "n_val_anomalous"} <= set(table.columns)


# Parse failures and cache hits are counted per condition.
def test_run_condition_counts(processed_synthetic) -> None:
    _, logs = processed_synthetic
    result = run_condition(logs, StubLLMClassifier(), "llm_stub", ["val", "test"])
    assert result.n_unparsed == int(1 in {d.template_id for d in result.decisions})
    assert result.n_cached == sum(d.template_id % 2 == 0 for d in result.decisions)
