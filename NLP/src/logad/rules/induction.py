"""Rule induction, verification and ranking (LogRules' induction stage).

1. **Training pool.** Every template seen in the training period, labeled by
   the majority label of its training messages. Only template text is used -
   alert tags were removed during preprocessing.
2. **Induction.** The pool is shown to the generator LLM (GPT-4o-mini in
   LogRules) in small batches that mix normal and abnormal examples. For each
   batch the LLM proposes rules with keyword phrases. Several rounds with
   different shuffles give a diverse set of candidates.
3. **Verification.** Every candidate is applied to the *whole* training pool
   by keyword matching. A rule is kept only if it labels the templates it
   matches correctly (precision >= ``min_precision``, 1.0 by default: "keep
   the rules that produce correct results on the training set") and matches at
   least ``min_support`` of them.
4. **Ranking.** Kept rules are ranked by usage: how many training templates
   (then messages) they correctly cover.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import pandas as pd

from logad.llm.client import LLMClient
from logad.llm.parsing import extract_json, normalize_label
from logad.llm.prompts import PromptTemplate
from logad.rules.rule import LABEL_NAMES, Rule, is_informative_keyword, normalize_keyword
from logad.utils import get_logger

logger = get_logger(__name__)


# One labeled template from the training period.
@dataclass(frozen=True)
class TrainingExample:
    template_id: int
    text: str
    label: int  # majority label of the template's training messages
    n_messages: int  # how many training messages use this template


# Build the training pool from the template table: templates that occur in
# the training period, labeled abnormal if most of their training messages
# are alerts. Mixed templates therefore get their majority label.
def training_pool(templates: pd.DataFrame) -> list[TrainingExample]:
    seen = templates[templates["n_train"] > 0]
    return [
        TrainingExample(
            template_id=int(row.template_id),
            text=str(row.template),
            label=int(2 * row.n_train_anomalous > row.n_train),
            n_messages=int(row.n_train),
        )
        for row in seen.itertuples()
    ]


# Split the pool into induction batches.
#
# Each batch holds up to batch_size // 2 abnormal and batch_size // 2 normal
# examples (anomalous templates are rare, so they are cycled to keep batches
# balanced). One round covers every template at least once; `rounds` repeats
# this with fresh shuffles.
def make_batches(
    pool: list[TrainingExample], batch_size: int, rounds: int, seed: int
) -> list[list[TrainingExample]]:
    rng = np.random.default_rng(seed)
    by_label = {label: [e for e in pool if e.label == label] for label in (1, 0)}
    present = [label for label in (1, 0) if by_label[label]]
    if not present:
        return []
    per_label = max(1, batch_size // len(present))
    n_batches = math.ceil(max(len(by_label[label]) for label in present) / per_label)

    batches = []
    for _ in range(rounds):
        shuffled = {label: rng.permutation(len(by_label[label])) for label in present}
        for b in range(n_batches):
            batch = []
            for label in present:
                examples, order = by_label[label], shuffled[label]
                # Cycle through the (possibly shorter) list of this label.
                for i in range(b * per_label, (b + 1) * per_label):
                    candidate = examples[order[i % len(examples)]]
                    if candidate not in batch:
                        batch.append(candidate)
            rng.shuffle(batch)
            batches.append(batch)
    return batches


# Format a batch as numbered, labeled lines for the induction prompt.
def format_examples(batch: list[TrainingExample]) -> str:
    return "\n".join(
        f"{i}. [{LABEL_NAMES[e.label]}] {e.text}" for i, e in enumerate(batch, start=1)
    )


# Parse the generator's JSON answer into Rule objects, skipping malformed
# entries (missing text, unknown label, or no informative keyword).
def parse_candidate_rules(text: str) -> list[Rule]:
    data = extract_json(text)
    items = data.get("rules") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    rules = []
    for item in items:
        if not isinstance(item, dict):
            continue
        label = normalize_label(item.get("label"))
        keywords = item.get("keywords") or []
        if isinstance(keywords, str):
            keywords = [keywords]
        cleaned = tuple(
            dict.fromkeys(  # de-duplicate while keeping order
                normalize_keyword(str(k)) for k in keywords if is_informative_keyword(str(k))
            )
        )
        rule_text = str(item.get("rule", "")).strip()
        if label is None or not cleaned or not rule_text:
            continue
        rules.append(Rule(text=rule_text, keywords=cleaned, label=label))
    return rules


# Merge rules that share a label and keyword set, counting how many times
# each was proposed. The first wording seen is kept.
def deduplicate(rules: list[Rule]) -> list[Rule]:
    merged: dict[tuple[int, frozenset[str]], Rule] = {}
    for rule in rules:
        if rule.key in merged:
            merged[rule.key].proposals += rule.proposals
        else:
            merged[rule.key] = Rule(rule.text, rule.keywords, rule.label, proposals=rule.proposals)
    return list(merged.values())


# Ask the generator LLM for candidate rules, one prompt per batch.
# `progress` (optional) is called after every batch with (done, total).
def induce_rules(
    client: LLMClient,
    prompt: PromptTemplate,
    batches: list[list[TrainingExample]],
    max_rules_per_batch: int,
    progress: Callable[[int, int], None] | None = None,
) -> list[Rule]:
    candidates: list[Rule] = []
    for done, batch in enumerate(batches, start=1):
        messages = prompt.render(
            examples=format_examples(batch), max_rules=str(max_rules_per_batch)
        )
        proposed = parse_candidate_rules(client.complete(messages).text)
        if not proposed:
            logger.warning("Batch %d produced no parseable rules", done)
        candidates.extend(proposed[:max_rules_per_batch])
        if progress:
            progress(done, len(batches))
    return deduplicate(candidates)


# Apply every candidate to the whole training pool by keyword matching and
# record its support, correct matches and message coverage. Returns
# (kept rules in rank order, every candidate with its statistics).
def verify_rules(
    candidates: list[Rule],
    pool: list[TrainingExample],
    min_precision: float = 1.0,
    min_support: int = 1,
) -> tuple[list[Rule], list[Rule]]:
    for rule in candidates:
        matched = [e for e in pool if rule.matches(e.text)]
        correct = [e for e in matched if e.label == rule.label]
        rule.support = len(matched)
        rule.correct = len(correct)
        rule.message_coverage = sum(e.n_messages for e in correct)

    kept = [
        rule
        for rule in candidates
        if rule.correct >= min_support and rule.precision >= min_precision
    ]
    # Rank by usage: templates covered, then messages covered, then how often
    # the LLM proposed the rule; the text breaks remaining ties deterministically.
    kept.sort(key=lambda r: (-r.correct, -r.message_coverage, -r.proposals, r.text))
    return kept, candidates
