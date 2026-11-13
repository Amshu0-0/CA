"""Classifying log templates with an LLM (conditions 1-3 of Approach B).

Three prompting conditions isolate what each kind of guidance contributes:

* ``zero_shot``   the template alone - no rules, no examples
* ``few_shot``    plus labeled training examples ("case-based" prompting, the
                  comparison made in the LogRules paper)
* ``rule_based``  plus the induced, verified and ranked rules (LogRules'
                  deduction stage)

Every condition sees exactly the same template text (Drain template of the
message content; alert tags were removed in preprocessing). The prompts live
in ``prompts/<condition>.txt``. Each prompt ends with the log line, so all
text before it is identical across templates; a model server that caches
prompt prefixes (llama.cpp, vLLM) then only reads the new log each call.
"""

from __future__ import annotations

import numpy as np

from logad.evaluation.template_level import TemplateDecision
from logad.llm.client import LLMClient, Messages
from logad.llm.parsing import NORMAL, parse_classification
from logad.llm.prompts import PromptTemplate, load_prompt
from logad.rules.induction import TrainingExample
from logad.rules.rule import LABEL_NAMES, Rule

MODES: tuple[str, ...] = ("zero_shot", "few_shot", "rule_based")


# Pick `k` labeled training templates for the few-shot prompt: half abnormal
# and half normal (as far as available), chosen at random with a fixed seed so
# every template is classified with the same examples. Order is shuffled so
# labels do not follow a predictable pattern.
def select_few_shot_examples(
    pool: list[TrainingExample], k: int, seed: int
) -> list[TrainingExample]:
    rng = np.random.default_rng(seed)
    abnormal = [e for e in pool if e.label == 1]
    normal = [e for e in pool if e.label == 0]
    n_abnormal = min(len(abnormal), k // 2)
    n_normal = min(len(normal), k - n_abnormal)
    chosen = [abnormal[i] for i in rng.choice(len(abnormal), n_abnormal, replace=False)] + [
        normal[i] for i in rng.choice(len(normal), n_normal, replace=False)
    ]
    return [chosen[i] for i in rng.permutation(len(chosen))]


# Render few-shot examples as "Log: ... / Label: ..." blocks.
def format_few_shot_examples(examples: list[TrainingExample]) -> str:
    return "\n\n".join(f"Log: {e.text}\nLabel: {LABEL_NAMES[e.label]}" for e in examples)


# Render rules as a numbered list in rank order (numbers are what the model
# cites back in its "rule" field).
def format_rules(rules: list[Rule]) -> str:
    return "\n".join(f"{rank}. {rule.describe()}" for rank, rule in enumerate(rules, start=1))


# An LLM-based template classifier for one prompting condition.
class LLMTemplateClassifier:
    # `mode` is one of MODES. few_shot needs `examples`, rule_based needs
    # `rules`. `prompt` defaults to prompts/<mode>.txt.
    def __init__(
        self,
        client: LLMClient,
        mode: str,
        prompt: PromptTemplate | None = None,
        rules: list[Rule] | None = None,
        examples: list[TrainingExample] | None = None,
    ) -> None:
        if mode not in MODES:
            raise ValueError(f"Unknown mode {mode!r}; choose from {MODES}")
        self.client = client
        self.mode = mode
        self.prompt = prompt or load_prompt(mode)
        self.rules = rules or []

        # Values that are identical for every template are formatted once.
        self._context: dict[str, str] = {}
        if mode == "few_shot":
            if not examples:
                raise ValueError("few_shot mode needs labeled examples")
            self._context["examples"] = format_few_shot_examples(examples)
        elif mode == "rule_based":
            if not self.rules:
                raise ValueError("rule_based mode needs at least one rule")
            self._context["rules"] = format_rules(self.rules)

    # The chat messages sent for one template (also shown in the demo).
    def messages_for(self, text: str) -> Messages:
        return self.prompt.render(log=text, **self._context)

    # Ask the model about one template and turn its answer into a decision.
    # Unusable answers are labeled normal and marked parsed=False so they can
    # be counted; a cited rule number outside 1..len(rules) is discarded.
    def classify(self, template_id: int, text: str) -> TemplateDecision:
        response = self.client.complete(self.messages_for(text))
        answer = parse_classification(response.text)
        rule = answer.rule if self.mode == "rule_based" else None
        if rule is not None and not 1 <= rule <= len(self.rules):
            rule = None
        return TemplateDecision(
            template_id=template_id,
            label=answer.label if answer.label is not None else NORMAL,
            rule=rule,
            reason=answer.reason,
            parsed=answer.label is not None,
            latency_seconds=response.latency_seconds,
            cached=response.cached,
        )
