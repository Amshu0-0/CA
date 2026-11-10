"""Chronological train / validation / test split.

Messages are split by *time*, never at random: the earliest part of the log is
used for training, the next part for validation and the last part for testing.
Le & Zhang (ICSE 2022) showed that random splits let semi-supervised detectors
such as LogBERT peek at the future and inflate their scores; a chronological
split mimics deployment, where a model trained on the past must judge new logs.

Both approaches share this split: LogBERT trains on normal windows from the
training period, and rule induction uses labeled examples from the same period.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# Split names in chronological order.
SPLITS: tuple[str, ...] = ("train", "val", "test")


# The two timestamps that separate the three splits:
#   train:  timestamp <  train_end
#   val:    train_end <= timestamp < val_end
#   test:   timestamp >= val_end
@dataclass(frozen=True)
class SplitBoundaries:
    train_end: float
    val_end: float


# Work out the split boundaries for chronologically sorted `timestamps`.
#
# by="time":     the *timeline* is cut into fractions (e.g. the first 60% of
#                the time between the first and last message is training).
# by="messages": the *message count* is cut into fractions; the boundary is
#                the timestamp of the first message past the cut.
#
# Both modes return time boundaries, so a window can never straddle two splits.
def compute_split_boundaries(
    timestamps: np.ndarray,
    train: float = 0.6,
    val: float = 0.2,
    test: float = 0.2,
    by: str = "time",
) -> SplitBoundaries:
    if min(train, val, test) < 0 or not np.isclose(train + val + test, 1.0):
        raise ValueError(f"Split fractions must be >= 0 and sum to 1, got {train}/{val}/{test}")
    if len(timestamps) == 0:
        raise ValueError("Cannot split an empty dataset")
    if np.any(np.diff(timestamps) < 0):
        raise ValueError("Timestamps must be sorted chronologically before splitting")

    if by == "time":
        start, end = float(timestamps[0]), float(timestamps[-1])
        span = end - start
        return SplitBoundaries(train_end=start + train * span, val_end=start + (train + val) * span)

    if by == "messages":
        n = len(timestamps)
        # Index of the first message after each cut, clipped to the valid range.
        train_cut = min(int(round(n * train)), n - 1)
        val_cut = min(int(round(n * (train + val))), n - 1)
        return SplitBoundaries(
            train_end=float(timestamps[train_cut]), val_end=float(timestamps[val_cut])
        )

    raise ValueError(f"Unknown split mode {by!r}; use 'time' or 'messages'")


# Assign every message to "train", "val" or "test" according to `boundaries`.
# Returns an array of split names aligned with `timestamps`.
def assign_splits(timestamps: np.ndarray, boundaries: SplitBoundaries) -> np.ndarray:
    codes = np.zeros(len(timestamps), dtype=np.int8)  # 0 = train
    codes[timestamps >= boundaries.train_end] = 1  # val
    codes[timestamps >= boundaries.val_end] = 2  # test
    return np.asarray(SPLITS, dtype=object)[codes]
