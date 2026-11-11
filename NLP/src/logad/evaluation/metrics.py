"""Binary classification metrics and the shared window-level evaluation unit.

The project's research question compares methods that work at different
granularities: LogBERT judges whole windows, while the rule-guided LLM judges
individual templates/messages. To compare them fairly, every method is
reduced to one prediction per **5-minute test window**:

* LogBERT predicts windows directly.
* Message-level methods flag a window if **any** of its messages is flagged
  (``windows_from_message_flags``), mirroring how the ground truth is defined.

Metrics follow the usual definitions with "anomalous" as the positive class:
precision = TP / (TP + FP), recall = TP / (TP + FN), F1 = harmonic mean.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from logad.data.windows import count_in_windows


# Confusion-matrix counts plus the metrics derived from them.
@dataclass(frozen=True)
class BinaryMetrics:
    tp: int
    fp: int
    tn: int
    fn: int

    # Share of flagged items that are truly anomalous (0 if nothing is flagged).
    @property
    def precision(self) -> float:
        flagged = self.tp + self.fp
        return self.tp / flagged if flagged else 0.0

    # Share of anomalous items that were flagged (0 if there are none).
    @property
    def recall(self) -> float:
        positives = self.tp + self.fn
        return self.tp / positives if positives else 0.0

    # Harmonic mean of precision and recall (0 if both are 0).
    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0

    # Share of normal items wrongly flagged: FP / (FP + TN).
    @property
    def false_positive_rate(self) -> float:
        negatives = self.fp + self.tn
        return self.fp / negatives if negatives else 0.0

    # Total number of evaluated items.
    @property
    def support(self) -> int:
        return self.tp + self.fp + self.tn + self.fn

    # Counts and derived metrics as one flat, JSON-friendly dict.
    def as_dict(self) -> dict[str, float | int]:
        return {
            **asdict(self),
            "precision": self.precision,
            "recall": self.recall,
            "f1": self.f1,
            "false_positive_rate": self.false_positive_rate,
            "support": self.support,
        }


# Compare 0/1 ground truth with 0/1 predictions and count the four outcomes.
def binary_metrics(y_true: np.ndarray, y_pred: np.ndarray) -> BinaryMetrics:
    y_true = np.asarray(y_true).astype(bool)
    y_pred = np.asarray(y_pred).astype(bool)
    if y_true.shape != y_pred.shape:
        raise ValueError(f"Shape mismatch: labels {y_true.shape} vs predictions {y_pred.shape}")
    return BinaryMetrics(
        tp=int(np.sum(y_true & y_pred)),
        fp=int(np.sum(~y_true & y_pred)),
        tn=int(np.sum(~y_true & ~y_pred)),
        fn=int(np.sum(y_true & ~y_pred)),
    )


# Lift message-level flags to window level: a window is flagged if at least
# one message in its [start_idx, end_idx) range is flagged. Uses prefix sums,
# so it is linear in the number of messages plus windows.
def windows_from_message_flags(message_flags: np.ndarray, windows: pd.DataFrame) -> np.ndarray:
    pairs = windows[["start_idx", "end_idx"]].to_numpy()
    counts = count_in_windows(np.asarray(message_flags, dtype=np.int64), pairs)
    return (counts > 0).astype(np.int8)


# Render {method name: metrics dict} as a Markdown table for READMEs and the
# report. `extra_columns` maps a key in each dict to a column header.
def metrics_table(
    rows: dict[str, dict[str, float]], extra_columns: dict[str, str] | None = None
) -> str:
    columns = {"precision": "Precision", "recall": "Recall", "f1": "F1", "fp": "FP"}
    columns.update(extra_columns or {})
    header = "| Method | " + " | ".join(columns.values()) + " |"
    divider = "|---|" + "---:|" * len(columns)
    lines = [header, divider]
    for name, values in rows.items():
        cells = []
        for key in columns:
            value = values.get(key, "")
            if isinstance(value, float) and key in ("precision", "recall", "f1"):
                cells.append(f"{value:.3f}")
            elif isinstance(value, float):
                cells.append(f"{value:.2f}")
            else:
                cells.append(str(value))
        lines.append(f"| {name} | " + " | ".join(cells) + " |")
    return "\n".join(lines)
