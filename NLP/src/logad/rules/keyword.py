"""Keyword baseline: the induced rules applied as plain string matching (no LLM).

This is condition 4 of Approach B. It asks whether the LLM adds anything
beyond the rules themselves: if the rule-based LLM is no better than matching
the same keywords, the rules - not the model - do the work.

Decision procedure (a ranked "decision list"): go through the rules in rank
order and return the label of the **first** rule whose keyword occurs in the
template. If no rule fires, the template is labeled normal, the majority class.
"""

from __future__ import annotations

from logad.evaluation.template_level import TemplateDecision
from logad.rules.rule import Rule


# Classifies template text with an ordered list of keyword rules.
class KeywordClassifier:
    def __init__(self, rules: list[Rule], default_label: int = 0) -> None:
        self.rules = rules
        self.default_label = default_label

    # Return the decision for one template. `rule` is the 1-based rank of the
    # rule that fired (None if none did) and `reason` the matching keyword.
    def classify(self, template_id: int, text: str) -> TemplateDecision:
        for rank, rule in enumerate(self.rules, start=1):
            keyword = rule.matching_keyword(text)
            if keyword is not None:
                return TemplateDecision(template_id, rule.label, rule=rank, reason=keyword)
        return TemplateDecision(template_id, self.default_label, rule=None, reason="no rule fired")
