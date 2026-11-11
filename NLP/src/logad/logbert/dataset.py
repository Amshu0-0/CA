"""Turning windows into token sequences, and masking them for training.

A 5-minute BGL window can hold thousands of messages during an alert storm,
more than the encoder's ``max_len``. Instead of truncating (LogBERT keeps only
the first ``seq_len`` keys, so the rest of the window is never inspected) we
split long windows into consecutive *chunks*. Every chunk becomes one sequence
``[<dist>, k1, k2, ...]``; at test time a window is anomalous if any of its
chunks is.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence

import numpy as np
import pandas as pd
import torch
from torch.utils.data import Dataset, Sampler

from logad.logbert.vocab import DIST_INDEX, MASK_INDEX, NUM_SPECIAL, PAD_INDEX, LogKeyVocab

# Label value that tells cross-entropy to ignore a position (PyTorch default).
IGNORE_INDEX = -100


# Split one encoded window into chunks of at most `max_len - 1` keys (one
# position is reserved for <dist>) and prepend <dist> to each chunk.
def window_to_chunks(encoded_keys: np.ndarray, max_len: int) -> list[np.ndarray]:
    if max_len < 2:
        raise ValueError("max_len must leave room for <dist> plus at least one key")
    body = max_len - 1
    chunks = []
    for start in range(0, len(encoded_keys), body):
        piece = encoded_keys[start : start + body]
        chunks.append(np.concatenate([[DIST_INDEX], piece]).astype(np.int64))
    return chunks


# Build token sequences for a set of windows.
#
# Returns (sequences, owner): `sequences` is a list of int64 arrays, and
# `owner[i]` is the position (0..len(windows)-1) of the window that produced
# sequences[i]. Chunks shorter than `min_keys` are dropped (used to skip very
# short training sequences, as LogBERT's min_len does).
def build_sequences(
    template_ids: np.ndarray,
    windows: pd.DataFrame,
    vocab: LogKeyVocab,
    max_len: int,
    min_keys: int = 1,
) -> tuple[list[np.ndarray], np.ndarray]:
    sequences: list[np.ndarray] = []
    owner: list[int] = []
    starts = windows["start_idx"].to_numpy()
    ends = windows["end_idx"].to_numpy()
    for position, (start, end) in enumerate(zip(starts, ends, strict=True)):
        encoded = vocab.encode(template_ids[start:end])
        for chunk in window_to_chunks(encoded, max_len):
            if len(chunk) - 1 >= min_keys:  # -1: do not count <dist>
                sequences.append(chunk)
                owner.append(position)
    return sequences, np.asarray(owner, dtype=np.int64)


# Choose which key positions of a sequence to mask.
#
# Every position except <dist> (index 0) is masked independently with
# probability `ratio`; at least one position is always masked so that each
# sequence contributes to the loss / to the anomaly decision.
def choose_mask_positions(length: int, ratio: float, rng: np.random.Generator) -> np.ndarray:
    if length < 2:
        return np.empty(0, dtype=np.int64)
    positions = np.flatnonzero(rng.random(length - 1) < ratio) + 1
    if positions.size == 0:
        positions = np.array([rng.integers(1, length)], dtype=np.int64)
    return positions


# Training / validation dataset for the masked log-key prediction task.
#
# Masking follows BERT (and LogBERT's training code): of the selected
# positions 80% become <mask>, 10% a random key and 10% stay unchanged; the
# label is the original key at selected positions and IGNORE_INDEX elsewhere.
#
# deterministic=False draws fresh masks every epoch (training).
# deterministic=True seeds the mask of item i with (seed, i), so the dev loss
# is computed on identical masks every epoch and stays comparable.
class MaskedSequenceDataset(Dataset):
    def __init__(
        self,
        sequences: Sequence[np.ndarray],
        vocab_size: int,
        mask_ratio: float = 0.5,
        deterministic: bool = False,
        seed: int = 0,
    ) -> None:
        self.sequences = sequences
        self.vocab_size = vocab_size
        self.mask_ratio = mask_ratio
        self.deterministic = deterministic
        self.seed = seed
        self._rng = np.random.default_rng(seed)

    def __len__(self) -> int:
        return len(self.sequences)

    # Return (input_ids, labels) for sequence `index` as int64 tensors.
    def __getitem__(self, index: int) -> tuple[torch.Tensor, torch.Tensor]:
        tokens = self.sequences[index].copy()
        rng = np.random.default_rng([self.seed, index]) if self.deterministic else self._rng

        labels = np.full(len(tokens), IGNORE_INDEX, dtype=np.int64)
        positions = choose_mask_positions(len(tokens), self.mask_ratio, rng)
        labels[positions] = tokens[positions]

        # 80/10/10 replacement of the selected positions.
        action = rng.random(len(positions))
        to_mask = positions[action < 0.8]
        to_random = positions[(action >= 0.8) & (action < 0.9)]
        tokens[to_mask] = MASK_INDEX
        if to_random.size and self.vocab_size > NUM_SPECIAL:
            tokens[to_random] = rng.integers(NUM_SPECIAL, self.vocab_size, size=to_random.size)
        # (the remaining 10% keep their original key)

        return torch.from_numpy(tokens), torch.from_numpy(labels)


# Pad a batch of variable-length sequences to the longest one.
# Returns a dict with input_ids, labels and attention_mask (True = real token).
def collate_batch(batch: list[tuple[torch.Tensor, torch.Tensor]]) -> dict[str, torch.Tensor]:
    max_len = max(len(tokens) for tokens, _ in batch)
    input_ids = torch.full((len(batch), max_len), PAD_INDEX, dtype=torch.long)
    labels = torch.full((len(batch), max_len), IGNORE_INDEX, dtype=torch.long)
    for row, (tokens, target) in enumerate(batch):
        input_ids[row, : len(tokens)] = tokens
        labels[row, : len(target)] = target
    return {"input_ids": input_ids, "labels": labels, "attention_mask": input_ids != PAD_INDEX}


# Batch sampler that groups sequences of similar length.
#
# BGL windows range from 1 to 511 keys; random batches would mostly be
# padding. We shuffle, cut the order into pools of `pool_batches` batches,
# sort each pool by length and slice it into batches, then shuffle the batch
# order. Batches stay random across epochs but contain similar lengths.
class LengthBucketSampler(Sampler[list[int]]):
    def __init__(
        self,
        lengths: Sequence[int],
        batch_size: int,
        shuffle: bool = True,
        pool_batches: int = 50,
        seed: int = 0,
    ) -> None:
        self.lengths = np.asarray(lengths)
        self.batch_size = batch_size
        self.shuffle = shuffle
        self.pool_size = batch_size * pool_batches
        self._rng = np.random.default_rng(seed)

    def __iter__(self) -> Iterator[list[int]]:
        order = (
            self._rng.permutation(len(self.lengths))
            if self.shuffle
            else np.arange(len(self.lengths))
        )
        batches = []
        for start in range(0, len(order), self.pool_size):
            pool = order[start : start + self.pool_size]
            pool = pool[np.argsort(self.lengths[pool], kind="stable")]
            batches.extend(
                pool[i : i + self.batch_size].tolist() for i in range(0, len(pool), self.batch_size)
            )
        if self.shuffle:
            self._rng.shuffle(batches)
        yield from batches

    # Number of batches per epoch: full pools hold exactly pool_size /
    # batch_size batches; the last, partial pool holds ceil(rest / batch_size).
    def __len__(self) -> int:
        full_pools, remainder = divmod(len(self.lengths), self.pool_size)
        return full_pools * (self.pool_size // self.batch_size) + -(-remainder // self.batch_size)
