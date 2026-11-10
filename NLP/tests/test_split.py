"""Tests for the chronological train/validation/test split."""

from __future__ import annotations

import numpy as np
import pytest

from logad.data.split import assign_splits, compute_split_boundaries


# by="time" cuts the timeline, regardless of how many messages fall where.
def test_split_by_time() -> None:
    timestamps = np.arange(0, 101)  # 0..100 s, one message per second
    bounds = compute_split_boundaries(timestamps, 0.6, 0.2, 0.2, by="time")
    assert (bounds.train_end, bounds.val_end) == (60.0, 80.0)

    splits = assign_splits(timestamps, bounds)
    assert (splits == "train").sum() == 60  # t = 0..59
    assert (splits == "val").sum() == 20  # t = 60..79
    assert (splits == "test").sum() == 21  # t = 80..100


# by="messages" cuts the message count, so dense periods move the boundary.
def test_split_by_messages_with_uneven_density() -> None:
    # 80 messages in the first 10 s, then 20 messages spread over 90 s.
    timestamps = np.concatenate([np.linspace(0, 10, 80), np.linspace(11, 100, 20)])
    by_time = assign_splits(timestamps, compute_split_boundaries(timestamps, by="time"))
    by_count = assign_splits(timestamps, compute_split_boundaries(timestamps, by="messages"))
    # by time: every message before t = 60 (all 80 dense ones + 11 sparse ones)
    assert (by_time == "train").sum() == (timestamps < 60).sum() == 91
    assert (by_count == "train").sum() == 60  # first 60% of the messages


# Splits are contiguous and ordered train -> val -> test.
def test_splits_are_chronological() -> None:
    rng = np.random.default_rng(0)
    timestamps = np.sort(rng.integers(0, 10_000, size=500))
    splits = assign_splits(timestamps, compute_split_boundaries(timestamps))
    order = {"train": 0, "val": 1, "test": 2}
    codes = np.array([order[s] for s in splits])
    assert np.all(np.diff(codes) >= 0)


# Bad inputs are rejected with clear errors.
def test_invalid_inputs() -> None:
    timestamps = np.arange(10)
    with pytest.raises(ValueError, match="sum to 1"):
        compute_split_boundaries(timestamps, 0.5, 0.2, 0.2)
    with pytest.raises(ValueError, match="sorted"):
        compute_split_boundaries(timestamps[::-1])
    with pytest.raises(ValueError, match="Unknown split mode"):
        compute_split_boundaries(timestamps, by="random")
    with pytest.raises(ValueError, match="empty"):
        compute_split_boundaries(np.array([]))
