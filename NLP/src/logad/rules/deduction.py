"""Running Approach B's four conditions (LogRules' deduction stage).

For one condition (zero-shot, few-shot, rule-based or keyword) this module

1. classifies every template that occurs in the evaluation splits, once,
2. copies each template's label to its messages and scores the result on the
   shared windows (and per message), and
3. measures inference time: for every split, the time spent classifying the
   templates that occur in it. For cached LLM answers the latency of the
   original call is used, so timing does not depend on cache state.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any, Protocol

import pandas as pd

from logad.data.processed import ProcessedLogs
from logad.evaluation.template_level import (
    TemplateDecision,
    decisions_frame,
    evaluate_template_labels,
    templates_in_splits,
)

# Condition name -> method name used in result files and tables.
CONDITION_METHODS: dict[str, str] = {
    "zero_shot": "llm_zero_shot",
    "few_shot": "llm_few_shot",
    "rule_based": "llm_rule_based",
    "keyword": "keyword_rules",
}


# Anything with a classify(template_id, text) method (keyword or LLM).
class TemplateClassifier(Protocol):
    def classify(self, template_id: int, text: str) -> TemplateDecision: ...


# Everything one condition produced.
@dataclass
class ConditionResult:
    method: str
    decisions: list[TemplateDecision]
    # split -> output of evaluate_template_labels (window + message metrics)
    evaluations: dict[str, dict[str, Any]]
    # split -> seconds spent classifying the templates that occur in it
    inference_seconds: dict[str, float]

    # Number of LLM answers that could not be parsed.
    @property
    def n_unparsed(self) -> int:
        return sum(not d.parsed for d in self.decisions)

    # Number of answers served from the LLM cache.
    @property
    def n_cached(self) -> int:
        return sum(d.cached for d in self.decisions)


# Classify each template once, timing every call. A cached LLM answer is
# charged the latency of the original request instead of the cache lookup.
def classify_templates(
    classifier: TemplateClassifier,
    template_ids: list[int],
    template_text: dict[int, str],
    progress: Callable[[int, int], None] | None = None,
) -> list[TemplateDecision]:
    decisions = []
    for done, template_id in enumerate(template_ids, start=1):
        start = time.perf_counter()
        decision = classifier.classify(template_id, template_text[template_id])
        elapsed = time.perf_counter() - start
        if not decision.cached:
            decision = replace(decision, latency_seconds=elapsed)
        decisions.append(decision)
        if progress:
            progress(done, len(template_ids))
    return decisions


# Run one condition end to end on `splits` (e.g. ["val", "test"]).
def run_condition(
    logs: ProcessedLogs,
    classifier: TemplateClassifier,
    method: str,
    splits: list[str],
    progress: Callable[[int, int], None] | None = None,
) -> ConditionResult:
    template_ids = templates_in_splits(logs, splits)
    decisions = classify_templates(classifier, template_ids, logs.template_text, progress)
    labels = {d.template_id: d.label for d in decisions}
    latency = {d.template_id: d.latency_seconds for d in decisions}

    evaluations = {split: evaluate_template_labels(logs, labels, split) for split in splits}
    inference_seconds = {
        split: float(sum(latency[t] for t in templates_in_splits(logs, [split])))
        for split in splits
    }
    return ConditionResult(method, decisions, evaluations, inference_seconds)


# Per-template table for error analysis and the demo: the decision next to
# the template text and its per-split ground-truth counts.
def decisions_table(result: ConditionResult, templates: pd.DataFrame) -> pd.DataFrame:
    columns = ["template_id", "template", "n_val", "n_val_anomalous", "n_test", "n_test_anomalous"]
    frame = decisions_frame(result.decisions)
    return frame.merge(templates[columns], on="template_id", how="left")
