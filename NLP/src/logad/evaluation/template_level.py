"""Evaluating methods that label *templates* (all of Approach B).

The rule-guided conditions classify each unique log template once and the
label is copied to every message with that template. This keeps LLM cost low
(BGL has a few hundred templates but millions of messages). The predictions
are then evaluated

* at **window level** (primary, shared with LogBERT): a window is flagged if
  any of its messages is flagged, and
* at **message level** (secondary, the setting of the LogRules paper).

Because a template-level label ignores parameter values, messages that share a
template but carry different ground-truth labels ("mixed" templates) cap what
any template-level method can achieve; the analysis reports how often this
happens.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import pandas as pd

from logad.data.processed import ProcessedLogs
from logad.evaluation.metrics import binary_metrics, windows_from_message_flags


# One method's verdict on one template.
@dataclass(frozen=True)
class TemplateDecision:
    template_id: int
    label: int  # predicted label: 1 abnormal, 0 normal
    rule: int | None = None  # 1-based rank of the rule that fired / the LLM cited
    reason: str = ""  # keyword that matched, or the LLM's justification
    parsed: bool = True  # False if an LLM answer was unusable (label defaults to normal)
    latency_seconds: float = 0.0  # time the decision took (LLM call latency)
    cached: bool = False  # True if the LLM answer came from the cache


# Collect decisions into a DataFrame (one row per template).
def decisions_frame(decisions: list[TemplateDecision]) -> pd.DataFrame:
    return pd.DataFrame([asdict(d) for d in decisions])


# Template IDs that occur in any of `splits`, in ascending order. These are
# the templates a method must classify to be evaluated on those splits.
def templates_in_splits(logs: ProcessedLogs, splits: list[str]) -> list[int]:
    in_splits = logs.messages["split"].astype(str).isin(splits).to_numpy()
    return sorted(int(t) for t in np.unique(logs.template_ids[in_splits]))


# Flag every message whose template was predicted abnormal. Templates without
# a decision are treated as normal.
def message_flags(logs: ProcessedLogs, template_labels: dict[int, int]) -> np.ndarray:
    abnormal = np.array([t for t, label in template_labels.items() if label == 1], dtype=np.int64)
    return np.isin(logs.template_ids, abnormal).astype(np.int8)


# Window- and message-level metrics of template predictions on one split.
# Returns a dict with "window" and "message" metric dicts and the window
# predictions (aligned with logs.windows_in(split)).
def evaluate_template_labels(
    logs: ProcessedLogs, template_labels: dict[int, int], split: str
) -> dict[str, Any]:
    flags = message_flags(logs, template_labels)
    windows = logs.windows_in(split)
    window_preds = windows_from_message_flags(flags, windows)

    in_split = (logs.messages["split"].astype(str) == split).to_numpy()
    return {
        "window": binary_metrics(windows["label"].to_numpy(), window_preds).as_dict(),
        "message": binary_metrics(logs.message_labels[in_split], flags[in_split]).as_dict(),
        "window_preds": window_preds,
    }
