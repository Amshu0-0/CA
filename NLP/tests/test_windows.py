"""Tests for sliding windows, including equivalence with LogBERT's original code."""

from __future__ import annotations

import numpy as np
import pytest

from logad.data.windows import build_window_table, count_in_windows, sliding_windows


# Line-by-line port of `sliding_window` from LogBERT's BGL preprocessing
# (HelenGuohx/logbert, scripts/bgl/data_process.py), reduced to the index
# bookkeeping. Used only as a reference to check our vectorised version.
def logbert_reference(time_data: list[int], window_size: int, step_size: int) -> set:
    log_size = len(time_data)
    pairs = set()
    start_time = time_data[0]
    end_time = start_time + window_size
    start_index = 0
    end_index = 0
    for cur_time in time_data:
        if cur_time < end_time:
            end_index += 1
        else:
            break
    pairs.add((start_index, end_index))

    while end_index < log_size:
        start_time = start_time + step_size
        end_time = start_time + window_size
        # The original code relies on the loop variable surviving the loop:
        # it ends as the first index past the boundary, or log_size if none.
        for i in range(start_index, log_size):
            if time_data[i] < start_time:
                i += 1
            else:
                break
        for j in range(end_index, log_size):
            if time_data[j] < end_time:
                j += 1
            else:
                break
        start_index = i
        end_index = j
        if start_index != end_index:
            pairs.add((start_index, end_index))
    return pairs


# Random bursty timelines: dense bursts separated by long silent gaps,
# which is what real BGL traffic looks like.
@pytest.mark.parametrize("seed", range(5))
@pytest.mark.parametrize(("size", "step"), [(300, 60), (60, 60), (120, 30)])
def test_matches_logbert_reference(seed: int, size: int, step: int) -> None:
    rng = np.random.default_rng(seed)
    gaps = np.where(rng.random(400) < 0.05, rng.integers(600, 5000, 400), rng.integers(0, 20, 400))
    timestamps = np.cumsum(gaps).astype(np.int64)

    pairs, starts = sliding_windows(timestamps, size, step)
    assert set(map(tuple, pairs.tolist())) == logbert_reference(timestamps.tolist(), size, step)
    assert len(pairs) == len(starts)
    assert np.all(np.diff(starts) > 0)  # chronological order


# Hand-checked example: messages at t = 0, 10, 70, 400 with 120 s / 60 s windows.
def test_small_example() -> None:
    pairs, starts = sliding_windows(np.array([0, 10, 70, 400]), 120, 60)
    # [0,120) -> msgs 0-2, [60,180) -> msg 2, [120..300) empty, [300,420) -> msg 3
    assert pairs.tolist() == [[0, 3], [2, 3], [3, 4]]
    assert starts.tolist() == [0, 60, 300]


# Edge cases: no messages, a single message, invalid parameters.
def test_edge_cases() -> None:
    pairs, starts = sliding_windows(np.array([], dtype=np.int64))
    assert pairs.shape == (0, 2) and starts.size == 0
    pairs, _ = sliding_windows(np.array([42]))
    assert pairs.tolist() == [[0, 1]]
    with pytest.raises(ValueError):
        sliding_windows(np.array([1, 2]), size_seconds=0)


# Prefix sums count the anomalous messages inside every window.
def test_count_in_windows() -> None:
    labels = np.array([0, 1, 0, 1, 1])
    pairs = np.array([[0, 2], [2, 3], [1, 5]])
    assert count_in_windows(labels, pairs).tolist() == [1, 0, 3]


# The window table never mixes splits, and a window is anomalous if it
# contains at least one alert (LogBERT's labeling convention).
def test_build_window_table() -> None:
    timestamps = np.arange(0, 600, 10)  # 60 messages over 10 minutes
    labels = np.zeros(60, dtype=np.int8)
    labels[45] = 1  # one alert at t = 450 s
    splits = np.array(["train"] * 30 + ["val"] * 15 + ["test"] * 15, dtype=object)

    table = build_window_table(timestamps, labels, splits, size_seconds=120, step_seconds=60)
    assert list(table["window_id"]) == list(range(len(table)))
    for split, (lo, hi) in {"train": (0, 30), "val": (30, 45), "test": (45, 60)}.items():
        part = table[table["split"] == split]
        assert part["start_idx"].min() >= lo and part["end_idx"].max() <= hi

    test_windows = table[table["split"] == "test"]
    covers_alert = (test_windows["start_idx"] <= 45) & (test_windows["end_idx"] > 45)
    assert (test_windows["label"] == covers_alert.astype(int)).all()
    assert table.loc[table["split"] != "test", "label"].sum() == 0
