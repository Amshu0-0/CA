# The two reference detectors that the processor runs next to LogBERT: the keyword rules and the saved decisions of the rule-guided LLM.
#
# They come from the first version of the NLP project, which compared LogBERT with a rule-guided language model. The NLP project (logad) is
# LogBERT only now, so the little code that the processor needs to apply the ranked keyword rules to one template is kept here, next to the
# engine that uses it. The rules (rules.json) and the decisions (llm_rule_based_decisions.csv) are the files of that experiment, unchanged;
# the folder reference/ keeps the originals, and scripts/export-nlp-bundle.py copies them into bundle/. Nothing in the cluster calls a
# language model: a saved decision is only looked up.
#
# A rule is a sentence plus keyword phrases, for example
#   {"text": "Logs reporting a data TLB error interrupt indicate a kernel failure.", "keywords": ["data tlb error interrupt"], "label": "abnormal"}
# The keyword detector goes through the rules in rank order and returns the label of the FIRST rule with a keyword in the template
# (case-insensitive); if no rule fires, the template is normal.

import json
from dataclasses import dataclass

LABEL_IDS = {"abnormal": 1, "normal": 0}


# What one detector says about one template: label 1 = abnormal, 0 = normal; rule = the rank (from 1) of the rule that fired, reason = its keyword.
@dataclass(frozen=True)
class Decision:
    template_id: int
    label: int
    rule: int | None = None
    reason: str = ""


# One ranked rule. Only what the processor needs: the keywords and the label.
@dataclass
class Rule:
    text: str
    keywords: tuple
    label: int

    # The first keyword that occurs in the text (case-insensitive), or None.
    def matching_keyword(self, text):
        lowered = text.lower()
        return next((k for k in self.keywords if k in lowered), None)


# The rules of a rules.json, in rank order.
def load_rules(path):
    with open(path, encoding="utf-8") as f:
        return [Rule(text=item["text"], keywords=tuple(item["keywords"]), label=LABEL_IDS[item["label"]]) for item in json.load(f)]


# The decision list: the first rule that fits decides, and a template that no rule fits is normal.
class KeywordClassifier:
    def __init__(self, rules, default_label=0):
        self.rules = rules
        self.default_label = default_label

    def classify(self, template_id, text):
        for rank, rule in enumerate(self.rules, start=1):
            keyword = rule.matching_keyword(text)

            if keyword is not None:
                return Decision(template_id, rule.label, rule=rank, reason=keyword)

        return Decision(template_id, self.default_label, rule=None, reason="no rule fired")
