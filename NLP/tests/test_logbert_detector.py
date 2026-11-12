"""Tests for LogBERT's detection rule, (g, r) tuning and prediction files."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import torch

from logad.evaluation.predictions import (
    load_method_results,
    load_window_predictions,
    save_method_result,
    save_window_predictions,
)
from logad.logbert.detector import LogBERTDetector, WindowScores, tune_thresholds
from logad.logbert.model import LogBERT
from logad.logbert.trainer import LogBERTTrainer, build_model, prepare_training_data
from logad.logbert.vocab import DIST_INDEX, UNK_INDEX, LogKeyVocab


# Two windows: window 0 has chunks with ranks [0, 5] and [9];
# window 1 has a single chunk with ranks [0, 0, 1].
def _scores() -> WindowScores:
    return WindowScores(
        ranks=np.array([0, 5, 9, 0, 0, 1]),
        chunk_offsets=np.array([0, 2, 3]),
        chunk_window=np.array([0, 0, 1]),
        n_windows=2,
        seconds=0.0,
    )


# Misses are counted per chunk, and a window is flagged if any chunk is.
def test_window_scores_decision_rule() -> None:
    scores = _scores()
    assert scores.chunk_misses(g=2).tolist() == [1, 1, 0]
    assert scores.predict(g=2, r=0).tolist() == [1, 0]
    assert scores.predict(g=1, r=0).tolist() == [1, 1]  # rank 1 >= 1 -> missed
    assert scores.predict(g=10, r=0).tolist() == [0, 0]
    assert scores.max_misses(g=1).tolist() == [1, 1]


# Tuning picks the setting with the best F1 on the labeled windows.
def test_tune_thresholds_picks_best_f1() -> None:
    choice = tune_thresholds(_scores(), labels=np.array([1, 0]), g_values=[1, 2, 10], r_max=1)
    assert (choice.g, choice.r) == (2, 0)
    assert choice.metrics.f1 == 1.0
    assert len(choice.grid) == 3 * 2


# Without anomalies the most conservative setting (no false positives) wins.
def test_tune_thresholds_without_anomalies() -> None:
    choice = tune_thresholds(_scores(), labels=np.array([0, 0]), g_values=[1, 2, 10], r_max=1)
    assert choice.metrics.fp == 0


# Unseen keys can never be predicted, so their rank is len(vocab).
def test_unknown_keys_are_always_missed() -> None:
    torch.manual_seed(0)
    vocab = LogKeyVocab([10, 11, 12])
    model = LogBERT(len(vocab), hidden=16, layers=1, heads=2, ff_dim=32, max_len=8)
    detector = LogBERTDetector(model, vocab, mask_ratio=0.99, full_coverage=True)
    sequence = np.array([DIST_INDEX, 4, UNK_INDEX, 5, 6])
    (prediction,) = detector.predict_sequences([sequence], top_k=2)
    unk = prediction.true_tokens == UNK_INDEX
    assert prediction.ranks[unk].tolist() == [len(vocab)]
    assert (prediction.ranks[~unk] < len(vocab)).all()
    assert prediction.top_tokens.shape == (4, 2)


# Full coverage masks every key exactly once; results are reproducible.
@pytest.mark.parametrize("mask_ratio", [0.5, 0.3, 0.15])
def test_full_coverage_masks_every_position_once(mask_ratio: float) -> None:
    vocab = LogKeyVocab(range(1, 30))
    model = LogBERT(len(vocab), hidden=16, layers=1, heads=2, ff_dim=32, max_len=64)
    detector = LogBERTDetector(model, vocab, mask_ratio=mask_ratio, full_coverage=True)
    sequence = np.concatenate([[DIST_INDEX], vocab.encode(np.arange(1, 30))])
    first, second = detector.predict_sequences([sequence, sequence])
    assert first.positions.tolist() == list(range(1, len(sequence)))
    assert np.array_equal(first.ranks, detector.predict_sequences([sequence])[0].ranks)
    assert np.array_equal(first.true_tokens, sequence[1:])
    assert second.positions.tolist() == first.positions.tolist()


# End to end on the synthetic log: the alert template only appears after
# training, so it is <unk>; with full coverage every alert window is caught.
def test_detector_catches_unseen_alerts(processed_synthetic) -> None:
    config, logs = processed_synthetic
    torch.manual_seed(0)
    data = prepare_training_data(logs, config.logbert, seed=0)
    model = build_model(len(data.vocab), config.logbert)
    LogBERTTrainer(model, config.logbert, torch.device("cpu")).fit(
        data, Path(config.paths.output_dir) / "model.pt"
    )
    detector = LogBERTDetector(model, data.vocab, full_coverage=True)

    val_windows = logs.windows_in("val")
    scores = detector.score_windows(logs.template_ids, val_windows)
    assert scores.n_windows == len(val_windows)
    labels = val_windows["label"].to_numpy()
    # With g = len(vocab) only <unk> keys count as misses.
    predictions = scores.predict(g=len(data.vocab), r=0)
    assert predictions[labels == 1].all()
    assert not predictions[labels == 0].any()


# Prediction and result files round-trip through disk.
def test_prediction_files_roundtrip(tmp_path: Path) -> None:
    save_window_predictions(tmp_path, "demo", np.array([3, 4]), np.array([1, 0]))
    frame = load_window_predictions(tmp_path, "demo")
    assert frame["window_id"].tolist() == [3, 4]
    assert frame["pred"].tolist() == [1, 0]
    assert frame["score"].isna().all()

    save_method_result(tmp_path, "demo", {"test": {"f1": 0.5}})
    assert load_method_results(tmp_path)["demo"]["test"]["f1"] == 0.5
