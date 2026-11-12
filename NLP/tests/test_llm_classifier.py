"""Tests for the zero-shot, few-shot and rule-based LLM template classifiers.

A stub stands in for the LLM so prompt construction and answer handling can
be tested offline. It never produces reported results.
"""

from __future__ import annotations

import pytest

from logad.llm.classifier import (
    LLMTemplateClassifier,
    format_rules,
    select_few_shot_examples,
)
from logad.llm.client import LLMResponse
from logad.rules.induction import TrainingExample
from logad.rules.rule import Rule

POOL = [TrainingExample(i, f"normal message {i}", 0, 10) for i in range(10)] + [
    TrainingExample(100 + i, f"alert message {i}", 1, 5) for i in range(3)
]
RULES = [Rule("TLB errors are failures", ("tlb error",), 1), Rule("ok", ("corrected",), 0)]


# Stub LLM that returns a scripted answer and remembers the last prompt.
class ScriptedLLM:
    name = "stub:classifier"

    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.last_prompt = ""

    def settings(self) -> dict:
        return {}

    def complete(self, messages) -> LLMResponse:
        self.last_prompt = messages[-1]["content"]
        return LLMResponse(text=self.answer, latency_seconds=0.5, cached=False)


# Few-shot examples are balanced, deterministic and limited by availability.
def test_select_few_shot_examples() -> None:
    examples = select_few_shot_examples(POOL, k=8, seed=0)
    assert sum(e.label for e in examples) == 3  # only 3 abnormal templates exist
    assert len(examples) == 8
    assert examples == select_few_shot_examples(POOL, k=8, seed=0)


# Zero-shot prompts contain the log and nothing learned from training data.
def test_zero_shot_prompt_and_decision() -> None:
    llm = ScriptedLLM('{"label": "abnormal", "reason": "kernel failure"}')
    decision = LLMTemplateClassifier(llm, "zero_shot").classify(7, "data TLB error interrupt")
    assert "Log: data TLB error interrupt" in llm.last_prompt
    assert "Rules:" not in llm.last_prompt and "Examples:" not in llm.last_prompt
    assert "$" not in llm.last_prompt
    assert (decision.template_id, decision.label, decision.rule) == (7, 1, None)
    assert decision.reason == "kernel failure" and decision.latency_seconds == 0.5


# Few-shot prompts include the labeled examples.
def test_few_shot_prompt() -> None:
    llm = ScriptedLLM('{"label": "normal", "reason": "routine"}')
    examples = select_few_shot_examples(POOL, k=4, seed=1)
    LLMTemplateClassifier(llm, "few_shot", examples=examples).classify(1, "x")
    assert llm.last_prompt.count("Label: abnormal") == 2
    assert llm.last_prompt.count("Label: normal") == 2


# Rule-based prompts list numbered rules and keep the rule the model cites.
def test_rule_based_prompt_and_cited_rule() -> None:
    llm = ScriptedLLM('{"label": "abnormal", "rule": 1, "reason": "rule 1 applies"}')
    classifier = LLMTemplateClassifier(llm, "rule_based", rules=RULES)
    decision = classifier.classify(3, "data TLB error interrupt")
    assert format_rules(RULES) in llm.last_prompt
    assert '1. [abnormal] TLB errors are failures (keywords: "tlb error")' in llm.last_prompt
    assert decision.rule == 1

    llm.answer = '{"label": "abnormal", "rule": 9}'  # no rule 9 exists
    assert classifier.classify(3, "x").rule is None


# Unusable answers default to normal and are flagged as unparsed.
def test_unparseable_answer() -> None:
    decision = LLMTemplateClassifier(ScriptedLLM("I am not sure."), "zero_shot").classify(1, "x")
    assert decision.label == 0 and not decision.parsed


# Missing inputs for a mode are caught at construction time.
def test_mode_validation() -> None:
    llm = ScriptedLLM("{}")
    with pytest.raises(ValueError, match="Unknown mode"):
        LLMTemplateClassifier(llm, "two_shot")
    with pytest.raises(ValueError, match="examples"):
        LLMTemplateClassifier(llm, "few_shot")
    with pytest.raises(ValueError, match="rule"):
        LLMTemplateClassifier(llm, "rule_based")
