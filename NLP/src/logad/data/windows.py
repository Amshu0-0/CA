"""Sliding time windows over a chronologically sorted log.

LogBERT groups BGL messages into **5-minute windows that slide by 1 minute**,
and both approaches are evaluated on exactly these windows. Because messages
are sorted by time, a window is simply a contiguous index range
``[start_idx, end_idx)`` into the message table, which keeps millions of
overlapping windows cheap to store.

A window's ground-truth label follows LogBERT's preprocessing
(``max(label_data[start_index:end_index])`` in its ``sliding_window``): the
window is anomalous if it contains **at least one** alert message.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from logad.data.split import SPLITS


# Compute sliding windows over sorted integer `timestamps` (seconds).
#
# Returns (index_pairs, start_times):
#   index_pairs  int64 array of shape (n_windows, 2) with [start, end) ranges
#   start_times  int64 array with the start time of each window
#
# This reproduces LogBERT's `sliding_window` function, vectorised with
# binary search instead of a Python loop:
#   * the first window starts at the first message's timestamp,
#   * each window covers [start, start + size) and the start moves by `step`,
#   * windowing stops after the first window that reaches the last message,
#   * empty windows and duplicate index ranges are dropped.
def sliding_windows(
    timestamps: np.ndarray, size_seconds: int = 300, step_seconds: int = 60
) -> tuple[np.ndarray, np.ndarray]:
    if size_seconds <= 0 or step_seconds <= 0:
        raise ValueError("Window size and step must be positive")
    n = len(timestamps)
    if n == 0:
        return np.empty((0, 2), dtype=np.int64), np.empty(0, dtype=np.int64)

    timestamps = np.asarray(timestamps, dtype=np.int64)
    starts = np.arange(timestamps[0], timestamps[-1] + 1, step_seconds, dtype=np.int64)
    # First message at or after each window start, and first message at or
    # after each window end: together they give the [lo, hi) index range.
    lo = np.searchsorted(timestamps, starts, side="left")
    hi = np.searchsorted(timestamps, starts + size_seconds, side="left")

    # Like LogBERT, stop after the first window that covers the last message;
    # later windows would only repeat shrinking suffixes of the log.
    reaches_end = np.flatnonzero(hi >= n)
    if reaches_end.size:
        last = reaches_end[0] + 1
        starts, lo, hi = starts[:last], lo[:last], hi[:last]

    # Drop empty windows (gaps in the log longer than the window size).
    non_empty = hi > lo
    starts, pairs = starts[non_empty], np.stack([lo[non_empty], hi[non_empty]], axis=1)

    # lo and hi only ever grow, so duplicate ranges are always adjacent.
    duplicate = np.zeros(len(pairs), dtype=bool)
    duplicate[1:] = np.all(pairs[1:] == pairs[:-1], axis=1)
    return pairs[~duplicate].astype(np.int64), starts[~duplicate]


# Count anomalous messages in each [start, end) window using a prefix sum,
# which is O(1) per window instead of re-scanning the messages.
def count_in_windows(values: np.ndarray, index_pairs: np.ndarray) -> np.ndarray:
    prefix = np.concatenate([[0], np.cumsum(values, dtype=np.int64)])
    return prefix[index_pairs[:, 1]] - prefix[index_pairs[:, 0]]


# Build the window table for the whole dataset.
#
# Windows are built separately inside each split so that no window mixes
# training and test messages. `timestamps`, `labels` and `splits` must be
# aligned and sorted chronologically. Returns a DataFrame with columns:
#   window_id, split, start_idx, end_idx, start_time, n_messages,
#   n_anomalous, label (1 if the window holds at least one alert)
def build_window_table(
    timestamps: np.ndarray,
    labels: np.ndarray,
    splits: np.ndarray,
    size_seconds: int = 300,
    step_seconds: int = 60,
) -> pd.DataFrame:
    frames = []
    for split in SPLITS:
        positions = np.flatnonzero(splits == split)
        if positions.size == 0:
            continue
        first, last = positions[0], positions[-1] + 1
        # Sorted timestamps + time-based splits => each split is contiguous.
        if positions.size != last - first:
            raise ValueError(f"Messages of split {split!r} are not contiguous; sort them first")

        pairs, starts = sliding_windows(timestamps[first:last], size_seconds, step_seconds)
        pairs = pairs + first  # local -> global message indices
        n_anomalous = count_in_windows(labels, pairs)
        frames.append(
            pd.DataFrame(
                {
                    "split": split,
                    "start_idx": pairs[:, 0],
                    "end_idx": pairs[:, 1],
                    "start_time": starts,
                    "n_messages": pairs[:, 1] - pairs[:, 0],
                    "n_anomalous": n_anomalous,
                    "label": (n_anomalous > 0).astype(np.int8),
                }
            )
        )

    columns = ["split", "start_idx", "end_idx", "start_time", "n_messages", "n_anomalous", "label"]
    table = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=columns)
    table.insert(0, "window_id", np.arange(len(table), dtype=np.int64))
    return table
