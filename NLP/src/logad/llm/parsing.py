"""Extracting structured answers from LLM output.

We ask for a single JSON object, but small models sometimes wrap it in a
Markdown code fence, add a sentence before it, or ignore the format entirely.
These helpers recover the answer whenever it is unambiguous and report
failures explicitly instead of guessing, so the number of unparseable answers
can be reported per method.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

ABNORMAL, NORMAL = 1, 0

# Words accepted for each label (lower-case).
_ABNORMAL_WORDS = {"abnormal", "anomaly", "anomalous", "anomalies", "alert", "failure", "yes"}
_NORMAL_WORDS = {"normal", "benign", "no"}
_WORD = re.compile(r"\b(abnormal|anomal\w*|normal)\b", re.IGNORECASE)


# Find and decode the first balanced {...} object in `text`. Returns None if
# there is no valid JSON object. Handles code fences and surrounding prose.
def extract_json(text: str) -> dict[str, Any] | None:
    start = text.find("{")
    while start != -1:
        depth, in_string, escaped = 0, False, False
        for end in range(start, len(text)):
            char = text[end]
            if in_string:
                # Inside a string literal braces do not count.
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
            elif char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    try:
                        value = json.loads(text[start : end + 1])
                    except json.JSONDecodeError:
                        break  # not valid JSON; try the next "{"
                    return value if isinstance(value, dict) else None
        start = text.find("{", start + 1)
    return None


# Map a label value from JSON ("abnormal", "Normal", 1, true, ...) to 1/0.
def normalize_label(value: Any) -> int | None:
    if isinstance(value, bool):
        return ABNORMAL if value else NORMAL
    if isinstance(value, int | float):
        return ABNORMAL if value else NORMAL
    if isinstance(value, str):
        word = value.strip().lower()
        if word in _ABNORMAL_WORDS:
            return ABNORMAL
        if word in _NORMAL_WORDS:
            return NORMAL
    return None


# The classifier's decision for one log template.
@dataclass(frozen=True)
class ParsedAnswer:
    label: int | None  # 1 abnormal, 0 normal, None if the answer was unusable
    rule: int | None  # number of the rule the model says it applied (rule-based prompt)
    reason: str  # the model's one-line justification, if given
    from_json: bool  # False when the label had to be read from free text


# Parse a classification answer. JSON is preferred; if the model did not
# produce usable JSON, fall back to the first "abnormal"/"normal" word in the
# text (note that "abnormal" contains "normal", hence the word-level regex).
def parse_classification(text: str) -> ParsedAnswer:
    data = extract_json(text)
    if data is not None:
        label = normalize_label(data.get("label"))
        if label is not None:
            rule = data.get("rule")
            try:
                rule = int(rule) if rule is not None else None
            except (TypeError, ValueError):
                rule = None
            return ParsedAnswer(label, rule, str(data.get("reason", "")).strip(), from_json=True)

    match = _WORD.search(text)
    if match is None:
        return ParsedAnswer(None, None, text.strip()[:200], from_json=False)
    label = NORMAL if match.group(1).lower() == "normal" else ABNORMAL
    return ParsedAnswer(label, None, text.strip()[:200], from_json=False)
