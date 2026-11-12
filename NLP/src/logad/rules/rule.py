"""Detection rules: a natural-language statement plus keyword phrases.

A rule produced by rule induction looks like::

    {"rule": "Logs reporting a data TLB error interrupt indicate a kernel failure.",
     "keywords": ["data TLB error interrupt"],
     "label": "abnormal"}

The sentence is what the LLM reads in the rule-based prompt; the keywords make
the very same rule executable as plain, case-insensitive substring matching,
which is what the keyword baseline does. LogRules' published anomaly-detection
rules are simple phrase matches, so this lets us test whether the LLM adds
anything beyond the rules themselves.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from logad.utils import load_json, save_json

LABEL_NAMES = {1: "abnormal", 0: "normal"}
LABEL_IDS = {name: label for label, name in LABEL_NAMES.items()}

_WHITESPACE = re.compile(r"\s+")
# A keyword needs at least this many characters other than wildcards and
# punctuation; shorter ones ("<*>", "a", ":") would match almost everything.
_MIN_KEYWORD_CHARS = 3


# Canonical form used for matching and de-duplication: lower-case with runs
# of whitespace collapsed to one space.
def normalize_keyword(keyword: str) -> str:
    return _WHITESPACE.sub(" ", keyword).strip().lower()


# True if a keyword is specific enough to be useful for matching.
def is_informative_keyword(keyword: str) -> bool:
    letters = re.sub(r"<\*>|[^0-9a-zA-Z]", "", keyword)
    return len(letters) >= _MIN_KEYWORD_CHARS


# One detection rule and the statistics from verifying it on training data.
@dataclass
class Rule:
    text: str  # natural-language rule shown to the LLM
    keywords: tuple[str, ...]  # normalised phrases; the rule fires if any occurs
    label: int  # 1 = abnormal, 0 = normal
    # --- filled in by verification on the training templates ---
    support: int = 0  # training templates the rule fires on
    correct: int = 0  # ... whose training label equals the rule's label
    message_coverage: int = 0  # training messages in the correctly matched templates
    proposals: int = 1  # number of induction prompts that proposed this rule

    # Share of matched training templates the rule labels correctly.
    @property
    def precision(self) -> float:
        return self.correct / self.support if self.support else 0.0

    # Identity used to merge duplicate proposals: same label + same keywords.
    @property
    def key(self) -> tuple[int, frozenset[str]]:
        return self.label, frozenset(self.keywords)

    # The first keyword occurring in `text` (case-insensitive), or None.
    def matching_keyword(self, text: str) -> str | None:
        lowered = text.lower()
        return next((k for k in self.keywords if k in lowered), None)

    # True if any of the rule's keywords occurs in `text`.
    def matches(self, text: str) -> bool:
        return self.matching_keyword(text) is not None

    # One line as it appears in the rule-based prompt, e.g.
    # '[abnormal] Logs with "data TLB error" indicate ... (keywords: "data tlb error")'.
    def describe(self) -> str:
        keywords = ", ".join(f'"{k}"' for k in self.keywords)
        return f"[{LABEL_NAMES[self.label]}] {self.text} (keywords: {keywords})"

    # JSON-friendly representation (label stored as a word for readability).
    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["label"] = LABEL_NAMES[self.label]
        data["keywords"] = list(self.keywords)
        data["precision"] = round(self.precision, 4)
        return data

    # Rebuild a rule saved with `to_dict`.
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Rule:
        return cls(
            text=data["text"],
            keywords=tuple(data["keywords"]),
            label=LABEL_IDS[data["label"]],
            support=data.get("support", 0),
            correct=data.get("correct", 0),
            message_coverage=data.get("message_coverage", 0),
            proposals=data.get("proposals", 1),
        )


# Save rules (in rank order) as JSON.
def save_rules(rules: list[Rule], path: str | Path) -> None:
    save_json([rule.to_dict() for rule in rules], path)


# Load rules saved with `save_rules`, preserving their order.
def load_rules(path: str | Path) -> list[Rule]:
    return [Rule.from_dict(item) for item in load_json(path)]


# Render rules as a numbered Markdown table (for the README / report).
def rules_markdown(rules: list[Rule]) -> str:
    lines = [
        "| # | Label | Rule | Keywords | Train templates (correct/matched) | Train messages |",
        "|---:|---|---|---|---:|---:|",
    ]
    for rank, rule in enumerate(rules, start=1):
        keywords = ", ".join(f"`{k}`" for k in rule.keywords)
        text = rule.text.replace("|", "\\|")
        lines.append(
            f"| {rank} | {LABEL_NAMES[rule.label]} | {text} | {keywords} | "
            f"{rule.correct}/{rule.support} | {rule.message_coverage:,} |"
        )
    return "\n".join(lines)
