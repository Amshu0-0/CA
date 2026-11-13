"""Tests for LogBERT's training-data selection and training loop."""

from __future__ import annotations

import numpy as np
import pandas as pd
import torch

from logad.logbert.trainer import (
    LogBERTTrainer,
    build_model,
    keys_covered_by,
    load_checkpoint,
    prepare_training_data,
    unique_sequences,
)
from logad.logbert.vocab import NUM_SPECIAL


# The difference-array trick returns exactly the keys inside the windows.
def test_keys_covered_by() -> None:
    template_ids = np.array([5, 6, 7, 8, 9, 10])
    windows = pd.DataFrame({"start_idx": [0, 1], "end_idx": [2, 3]})  # covers 0..2
    assert keys_covered_by(template_ids, windows).tolist() == [5, 6, 7]


# Only exact duplicates collapse, and the first copies keep their order.
def test_unique_sequences() -> None:
    a, b = np.array([2, 4, 5]), np.array([2, 5, 4])
    kept = unique_sequences([a, b, a.copy(), np.array([2, 4, 5, 5]), b.copy()])
    assert [s.tolist() for s in kept] == [[2, 4, 5], [2, 5, 4], [2, 4, 5, 5]]


# With dedupe_sequences on, no training or dev sequence appears twice. The
# periodic synthetic log repeats itself, so many windows collapse.
def test_prepare_training_data_dedupe(processed_synthetic) -> None:
    config, logs = processed_synthetic
    plain = prepare_training_data(logs, config.logbert, seed=0)
    config.logbert.dedupe_sequences = True
    deduped = prepare_training_data(logs, config.logbert, seed=0)
    for sequences in (deduped.train_sequences, deduped.dev_sequences):
        assert len({s.tobytes() for s in sequences}) == len(sequences)
    assert 0 < len(deduped.train_sequences) < len(plain.train_sequences)
    assert deduped.vocab.keys == plain.vocab.keys


# Only normal training windows are used, and the dev set is their time tail.
def test_prepare_training_data(processed_synthetic) -> None:
    config, logs = processed_synthetic
    data = prepare_training_data(logs, config.logbert, seed=0)
    train_windows = logs.windows_in("train")
    n_normal = int((train_windows["label"] == 0).sum())

    assert len(data.train_sequences) + len(data.dev_sequences) >= n_normal
    assert len(data.dev_sequences) > 0
    # The alert template only occurs in the validation period -> not in the vocab.
    alert_id = logs.templates.loc[
        logs.templates["template"] == "data TLB error interrupt", "template_id"
    ].item()
    assert alert_id not in data.vocab.keys
    # Every training token is a known key (no <unk> in normal training data).
    assert all((seq[1:] >= NUM_SPECIAL).all() for seq in data.train_sequences)


# A few epochs on the periodic synthetic log reduce the loss and save a
# checkpoint that reloads into an equivalent model.
def test_training_reduces_loss_and_checkpoints(processed_synthetic, tmp_path) -> None:
    config, logs = processed_synthetic
    torch.manual_seed(0)
    data = prepare_training_data(logs, config.logbert, seed=0)
    model = build_model(len(data.vocab), config.logbert)
    trainer = LogBERTTrainer(model, config.logbert, torch.device("cpu"), seed=0)

    checkpoint = tmp_path / "model.pt"
    history = trainer.fit(data, checkpoint)
    assert history[-1]["train_mlkp"] < history[0]["train_mlkp"]
    assert checkpoint.exists()

    restored, vocab, center, raw = load_checkpoint(checkpoint)
    assert vocab.keys == data.vocab.keys
    assert center.shape == (config.logbert.hidden,)
    assert raw["config"]["hidden"] == config.logbert.hidden
    input_ids = torch.from_numpy(data.dev_sequences[0]).unsqueeze(0)
    with torch.no_grad():
        first, second = restored(input_ids), restored(input_ids)
    assert torch.equal(first.logits, second.logits)  # eval mode: deterministic


# The centre is the mean <dist> vector, one value per hidden unit.
def test_compute_center(processed_synthetic) -> None:
    config, logs = processed_synthetic
    data = prepare_training_data(logs, config.logbert, seed=0)
    trainer = LogBERTTrainer(
        build_model(len(data.vocab), config.logbert), config.logbert, torch.device("cpu")
    )
    center = trainer.compute_center(data.train_sequences)
    assert center.shape == (config.logbert.hidden,)
    assert torch.isfinite(center).all()
