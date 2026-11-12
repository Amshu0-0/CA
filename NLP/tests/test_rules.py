"""Tests for rules, rule induction, verification and ranking.

The generator LLM is replaced by a stub that returns a fixed JSON answer; it
tests the plumbing only and is never used to produce reported results.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from logad.llm.client import LLMResponse
from logad.llm.prompts import load_prompt
from logad.rules.induction import (
    TrainingExample,
    deduplicate,
    format_examples,
    induce_rules,
    make_batches,
    parse_candidate_rules,
    training_pool,
    verify_rules,
)
from logad.rules.rule import Rule, load_rules, rules_markdown, save_rules

POOL = [
    TrainingExample(1, "instruction cache parity error corrected", 0, 1000),
    TrainingExample(2, "data TLB error interrupt", 1, 50),
    TrainingExample(3, "data storage interrupt", 1, 20),
    TrainingExample(4, "generating core.<*>", 0, 300),
    TrainingExample(5, "ciod: failed to read message prefix on control stream", 1, 10),
]


# --------------------------------------------------------------------- rules


# Matching is case-insensitive substring search over the keywords.
def test_rule_matching() -> None:
    rule = Rule("TLB errors are failures", ("data tlb error",), 1)
    assert rule.matches("Data TLB Error interrupt")
    assert rule.matching_keyword("data TLB error interrupt") == "data tlb error"
    assert not rule.matches("instruction cache parity error corrected")


# Rules survive a save/load round trip, in order.
def test_rules_roundtrip(tmp_path: Path) -> None:
    rules = [Rule("a", ("interrupt",), 1, support=3, correct=3), Rule("b", ("corrected",), 0)]
    save_rules(rules, tmp_path / "rules.json")
    loaded = load_rules(tmp_path / "rules.json")
    assert [(r.text, r.keywords, r.label, r.correct) for r in loaded] == [
        ("a", ("interrupt",), 1, 3),
        ("b", ("corrected",), 0, 0),
    ]
    assert "| 1 | abnormal | a | `interrupt` | 3/3 |" in rules_markdown(loaded)


# ----------------------------------------------------------------- the pool


# Templates are labeled by the majority label of their training messages;
# templates never seen in training are excluded.
def test_training_pool_uses_majority_label() -> None:
    templates = pd.DataFrame(
        {
            "template_id": [1, 2, 3, 4],
            "template": ["a", "b", "c", "d"],
            "n_train": [10, 10, 10, 0],
            "n_train_anomalous": [0, 9, 5, 0],
        }
    )
    pool = training_pool(templates)
    assert [(e.template_id, e.label) for e in pool] == [(1, 0), (2, 1), (3, 0)]


# Batches mix both labels and every template appears in every round.
def test_make_batches_is_balanced_and_complete() -> None:
    batches = make_batches(POOL, batch_size=4, rounds=2, seed=0)
    for batch in batches:
        labels = {e.label for e in batch}
        assert labels == {0, 1}
        assert len(batch) <= 4
    round_size = len(batches) // 2
    for r in range(2):
        seen = {e.template_id for b in batches[r * round_size : (r + 1) * round_size] for e in b}
        assert seen == {e.template_id for e in POOL}


# Examples are shown as numbered "[label] text" lines.
def test_format_examples() -> None:
    assert format_examples(POOL[:2]) == (
        "1. [normal] instruction cache parity error corrected\n2. [abnormal] data TLB error interrupt"
    )


# ---------------------------------------------------------- parsing + dedup


# Valid rules are parsed; malformed or uninformative entries are dropped.
def test_parse_candidate_rules() -> None:
    answer = json.dumps(
        {
            "rules": [
                {"rule": "TLB errors fail", "keywords": ["Data  TLB error"], "label": "abnormal"},
                {"rule": "no keywords", "keywords": [], "label": "abnormal"},
                {"rule": "bad label", "keywords": ["x y z"], "label": "maybe"},
                {"rule": "only wildcards", "keywords": ["<*>", "::"], "label": "normal"},
                {"rule": "single string", "keywords": "corrected", "label": "normal"},
            ]
        }
    )
    rules = parse_candidate_rules("Here you go:\n" + answer)
    assert [(r.keywords, r.label) for r in rules] == [
        (("data tlb error",), 1),
        (("corrected",), 0),
    ]
    assert parse_candidate_rules("not json") == []


# Duplicate proposals are merged and counted.
def test_deduplicate() -> None:
    rules = deduplicate(
        [Rule("x", ("a b c",), 1), Rule("y", ("a b c",), 1), Rule("z", ("a b c",), 0)]
    )
    assert [(r.text, r.label, r.proposals) for r in rules] == [("x", 1, 2), ("z", 0, 1)]


# --------------------------------------------------------------- verification


# Rules that mislabel a training template are dropped; the rest are ranked
# by the number of templates (then messages) they cover correctly.
def test_verify_rules_filters_and_ranks() -> None:
    candidates = [
        Rule("errors are bad", ("error",), 1),  # also matches a normal template
        Rule("interrupts are bad", ("interrupt",), 1),  # 2 abnormal templates
        Rule("ciod failures", ("ciod: failed",), 1),  # 1 abnormal template
        Rule("cores are fine", ("generating core",), 0),  # 1 normal template, 300 msgs
        Rule("never fires", ("zzz",), 1),
    ]
    kept, scored = verify_rules(candidates, POOL, min_precision=1.0, min_support=1)
    assert [r.text for r in kept] == ["interrupts are bad", "cores are fine", "ciod failures"]
    # "error" occurs in one normal and one abnormal template -> precision 0.5.
    error_rule = next(r for r in scored if r.text == "errors are bad")
    assert (error_rule.support, error_rule.correct) == (2, 1)
    assert kept[0].message_coverage == 70

    relaxed, _ = verify_rules(candidates, POOL, min_precision=0.5, min_support=1)
    assert "errors are bad" in [r.text for r in relaxed]


# ------------------------------------------------------------ the full stage


# Stub generator returning the same rules for every batch.
class StubGenerator:
    name = "stub:generator"

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def settings(self) -> dict:
        return {}

    def complete(self, messages) -> LLMResponse:
        self.prompts.append(messages[-1]["content"])
        answer = {"rules": [{"rule": "TLB", "keywords": ["TLB error"], "label": "abnormal"}]}
        return LLMResponse(text=json.dumps(answer), latency_seconds=0.0)


# induce_rules sends one filled-in prompt per batch and merges duplicates.
def test_induce_rules_with_stub_generator() -> None:
    generator = StubGenerator()
    batches = make_batches(POOL, batch_size=4, rounds=1, seed=0)
    rules = induce_rules(generator, load_prompt("rule_induction"), batches, max_rules_per_batch=5)
    assert len(generator.prompts) == len(batches)
    assert "[abnormal] data TLB error interrupt" in "\n".join(generator.prompts)
    assert "$" not in generator.prompts[0]  # every placeholder was filled
    assert len(rules) == 1 and rules[0].proposals == len(batches)
