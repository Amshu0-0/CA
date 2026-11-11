"""Tests for LogBERT's vocabulary, sequence building, masking and model."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
import torch

from logad.logbert.dataset import (
    IGNORE_INDEX,
    LengthBucketSampler,
    MaskedSequenceDataset,
    build_sequences,
    choose_mask_positions,
    collate_batch,
    window_to_chunks,
)
from logad.logbert.model import LogBERT
from logad.logbert.vocab import DIST_INDEX, MASK_INDEX, NUM_SPECIAL, UNK_INDEX, LogKeyVocab

# ----------------------------------------------------------------- vocabulary


# Known keys map after the special tokens; unknown or invalid keys map to <unk>.
def test_vocab_encode_and_decode() -> None:
    vocab = LogKeyVocab([7, 3, 3, 12])
    assert len(vocab) == NUM_SPECIAL + 3
    encoded = vocab.encode([3, 7, 12, 99, 5, -1])
    assert encoded.tolist() == [4, 5, 6, UNK_INDEX, UNK_INDEX, UNK_INDEX]
    assert vocab.decode(5) == 7
    assert vocab.decode(UNK_INDEX) == "<unk>"


# The vocabulary survives a round trip through plain Python types.
def test_vocab_roundtrip() -> None:
    vocab = LogKeyVocab([1, 5, 9])
    restored = LogKeyVocab.from_dict(vocab.to_dict())
    assert restored.encode([1, 5, 9, 2]).tolist() == vocab.encode([1, 5, 9, 2]).tolist()


# ------------------------------------------------------------------ sequences


# Long windows are cut into chunks that each start with <dist>.
def test_window_to_chunks() -> None:
    chunks = window_to_chunks(np.arange(10, 20), max_len=5)  # 4 keys per chunk
    assert [c.tolist() for c in chunks] == [
        [DIST_INDEX, 10, 11, 12, 13],
        [DIST_INDEX, 14, 15, 16, 17],
        [DIST_INDEX, 18, 19],
    ]
    with pytest.raises(ValueError):
        window_to_chunks(np.arange(3), max_len=1)


# build_sequences remembers which window every chunk came from.
def test_build_sequences_tracks_owner_and_min_keys() -> None:
    template_ids = np.array([1, 2, 1, 2, 1, 3, 3])
    windows = pd.DataFrame({"start_idx": [0, 5], "end_idx": [5, 7]})
    vocab = LogKeyVocab([1, 2])
    sequences, owner = build_sequences(template_ids, windows, vocab, max_len=4)
    assert owner.tolist() == [0, 0, 1]
    assert sequences[2].tolist() == [DIST_INDEX, UNK_INDEX, UNK_INDEX]  # key 3 unseen

    _, owner = build_sequences(template_ids, windows, vocab, max_len=4, min_keys=3)
    assert owner.tolist() == [0]  # 2-key chunks are dropped


# ------------------------------------------------------------------- masking


# <dist> is never masked and at least one key always is.
def test_choose_mask_positions() -> None:
    rng = np.random.default_rng(0)
    for length in range(2, 30):
        positions = choose_mask_positions(length, ratio=0.01, rng=rng)
        assert positions.size >= 1
        assert positions.min() >= 1 and positions.max() < length
    assert choose_mask_positions(1, 0.5, rng).size == 0


# Labels hold the original key at masked positions and IGNORE elsewhere.
def test_masked_dataset_labels() -> None:
    sequence = np.array([DIST_INDEX, 4, 5, 6, 7, 8, 9, 10, 11], dtype=np.int64)
    dataset = MaskedSequenceDataset([sequence], vocab_size=12, mask_ratio=0.5, seed=1)
    tokens, labels = dataset[0]
    masked = labels != IGNORE_INDEX
    assert masked.any()
    assert tokens[0] == DIST_INDEX and labels[0] == IGNORE_INDEX
    assert torch.equal(labels[masked], torch.from_numpy(sequence)[masked])
    assert torch.equal(tokens[~masked], torch.from_numpy(sequence)[~masked])


# Deterministic mode gives identical masks on every call (stable dev loss).
def test_deterministic_masking_is_reproducible() -> None:
    sequence = np.arange(NUM_SPECIAL, NUM_SPECIAL + 40, dtype=np.int64)
    sequence[0] = DIST_INDEX
    dataset = MaskedSequenceDataset([sequence], vocab_size=60, deterministic=True, seed=3)
    first, second = dataset[0], dataset[0]
    assert torch.equal(first[0], second[0]) and torch.equal(first[1], second[1])
    assert (first[0] == MASK_INDEX).any()


# Padding fills the batch and the attention mask marks real tokens.
def test_collate_batch() -> None:
    batch = [
        (torch.tensor([2, 5, 6]), torch.tensor([-100, 5, -100])),
        (torch.tensor([2, 7]), torch.tensor([-100, 7])),
    ]
    out = collate_batch(batch)
    assert out["input_ids"].tolist() == [[2, 5, 6], [2, 7, 0]]
    assert out["labels"][1].tolist() == [-100, 7, -100]
    assert out["attention_mask"].tolist() == [[True, True, True], [True, True, False]]


# Every index is used exactly once per epoch and len() is exact.
@pytest.mark.parametrize("n", [1, 7, 64, 333])
def test_length_bucket_sampler(n: int) -> None:
    lengths = np.random.default_rng(0).integers(1, 50, size=n)
    sampler = LengthBucketSampler(lengths, batch_size=8, pool_batches=4, seed=0)
    batches = list(sampler)
    assert len(batches) == len(sampler)
    assert sorted(i for batch in batches for i in batch) == list(range(n))


# --------------------------------------------------------------------- model


# The model returns per-position key scores and one vector per sequence.
def test_model_output_shapes() -> None:
    model = LogBERT(vocab_size=20, hidden=32, layers=2, heads=4, ff_dim=64, max_len=16)
    input_ids = torch.randint(NUM_SPECIAL, 20, (3, 10))
    input_ids[:, 0] = DIST_INDEX
    output = model(input_ids)
    assert output.logits.shape == (3, 10, 20)
    assert output.dist.shape == (3, 32)
    with pytest.raises(ValueError, match="max_len"):
        model(torch.ones(1, 17, dtype=torch.long))


# Padding a sequence inside a batch must not change its outputs.
def test_padding_does_not_change_outputs() -> None:
    torch.manual_seed(0)
    model = LogBERT(vocab_size=20, hidden=32, layers=2, heads=4, ff_dim=64, max_len=16).eval()
    short = torch.tensor([[DIST_INDEX, 5, 6, 7]])
    padded = torch.tensor([[DIST_INDEX, 5, 6, 7, 0, 0], [DIST_INDEX, 8, 9, 10, 11, 12]])
    with torch.no_grad():
        alone = model(short)
        batched = model(padded)
    assert torch.allclose(alone.logits[0], batched.logits[0, :4], atol=1e-5)
    assert torch.allclose(alone.dist[0], batched.dist[0], atol=1e-5)
