"""Anomaly detection with a trained LogBERT, and tuning of its two settings.

Detection rule (LogBERT, Section 3.3): mask some keys of a sequence and ask
the model to predict them. A masked key is *missed* if the true key is not
among the model's top-``g`` candidates. The sequence is anomalous if it has
**more than r missed keys**. A window is anomalous if any of its chunks is.

Instead of re-running the model for every (g, r) pair, we store the *rank* of
the true key at every masked position (0 = the model's best guess). For any
``g``, "missed" is simply ``rank >= g``, so the whole (g, r) grid on the
validation period is evaluated from one forward pass.

Masking modes
-------------
* LogBERT's original procedure (default) masks a random ``mask_ratio`` of the
  keys once. Keys that are not masked are never checked, so a single anomalous
  message has roughly a ``1 - mask_ratio`` chance of being ignored.
* ``full_coverage`` (our extension) splits the positions into
  ``ceil(1 / mask_ratio)`` disjoint groups and masks each group in turn, so
  every key is checked exactly once, at proportionally higher cost.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import torch

from logad.config import LogBERTDetectionConfig
from logad.evaluation.metrics import BinaryMetrics, binary_metrics
from logad.logbert.dataset import build_sequences, choose_mask_positions
from logad.logbert.model import LogBERT
from logad.logbert.trainer import load_checkpoint
from logad.logbert.vocab import MASK_INDEX, PAD_INDEX, UNK_INDEX, LogKeyVocab
from logad.utils import Timer


# What the model said about the masked keys of one sequence.
@dataclass
class MaskedPrediction:
    positions: np.ndarray  # masked positions in the sequence (1 = first key after <dist>)
    true_tokens: np.ndarray  # token index that was hidden at each position
    ranks: np.ndarray  # rank of the true key among all predictions (0 = top-1)
    top_tokens: np.ndarray  # (n_masked, top_k) best guesses; empty if top_k == 0

    # Number of masked keys outside the top-g candidates.
    def n_missed(self, g: int) -> int:
        return int(np.sum(self.ranks >= g))


# Ranks for every masked key of every chunk of a set of windows, stored as
# flat arrays so (g, r) decisions are vectorised.
@dataclass
class WindowScores:
    ranks: np.ndarray  # concatenated ranks of all chunks
    chunk_offsets: np.ndarray  # where each chunk's ranks start in `ranks`
    chunk_window: np.ndarray  # window position (0..n_windows-1) of each chunk
    n_windows: int
    seconds: float  # wall-clock inference time

    # Missed-key count of every chunk for candidate-set size g.
    def chunk_misses(self, g: int) -> np.ndarray:
        missed = (self.ranks >= g).astype(np.int64)
        return np.add.reduceat(missed, self.chunk_offsets)

    # A window is flagged if any of its chunks is flagged.
    def windows_from_chunks(self, chunk_flags: np.ndarray) -> np.ndarray:
        window_flags = np.zeros(self.n_windows, dtype=np.int8)
        np.maximum.at(window_flags, self.chunk_window, chunk_flags.astype(np.int8))
        return window_flags

    # Window predictions for settings (g, r): anomalous if > r missed keys.
    def predict(self, g: int, r: int) -> np.ndarray:
        return self.windows_from_chunks(self.chunk_misses(g) > r)

    # Continuous anomaly score per window: the largest missed-key count over
    # its chunks (used for error analysis and plots).
    def max_misses(self, g: int) -> np.ndarray:
        scores = np.zeros(self.n_windows, dtype=np.int64)
        np.maximum.at(scores, self.chunk_window, self.chunk_misses(g))
        return scores


# Runs a trained LogBERT over sequences and windows.
class LogBERTDetector:
    def __init__(
        self,
        model: LogBERT,
        vocab: LogKeyVocab,
        device: torch.device | str = "cpu",
        mask_ratio: float = 0.5,
        full_coverage: bool = False,
        batch_size: int = 128,
        seed: int = 1234,
    ) -> None:
        self.model = model.to(device).eval()
        self.vocab = vocab
        self.device = torch.device(device)
        self.mask_ratio = mask_ratio
        self.full_coverage = full_coverage
        self.batch_size = batch_size
        self.seed = seed

    # Build a detector from a checkpoint written by the trainer.
    @classmethod
    def from_checkpoint(
        cls,
        path: str | Path,
        detection: LogBERTDetectionConfig,
        device: torch.device | str = "cpu",
    ) -> LogBERTDetector:
        model, vocab, _, _ = load_checkpoint(path, device)
        return cls(
            model,
            vocab,
            device=device,
            mask_ratio=detection.mask_ratio,
            full_coverage=detection.full_coverage,
            batch_size=detection.batch_size,
            seed=detection.seed,
        )

    # The groups of positions to mask for a sequence of `length` tokens.
    # Original mode: one random group. Full coverage: disjoint groups that
    # together contain every key position exactly once.
    def _mask_groups(self, length: int, rng: np.random.Generator) -> list[np.ndarray]:
        if not self.full_coverage:
            return [choose_mask_positions(length, self.mask_ratio, rng)]
        n_groups = max(1, math.ceil(1.0 / self.mask_ratio))
        shuffled = rng.permutation(np.arange(1, length))
        return [np.sort(group) for group in np.array_split(shuffled, n_groups) if group.size]

    # Mask and predict every sequence in `sequences` (token-index arrays that
    # start with <dist>). Masks are seeded by (seed, sequence index), so the
    # same input always gives the same result. `top_k` > 0 also returns the
    # model's k best guesses per masked key (used by the demo).
    @torch.no_grad()
    def predict_sequences(
        self, sequences: list[np.ndarray], top_k: int = 0
    ) -> list[MaskedPrediction]:
        # One "instance" = one sequence with one group of positions masked.
        instances: list[tuple[int, np.ndarray]] = []
        for index, sequence in enumerate(sequences):
            rng = np.random.default_rng([self.seed, index])
            instances.extend((index, group) for group in self._mask_groups(len(sequence), rng))

        parts: list[list[tuple[np.ndarray, ...]]] = [[] for _ in sequences]
        # Sorting by length keeps padding (wasted compute) to a minimum.
        order = sorted(range(len(instances)), key=lambda i: len(sequences[instances[i][0]]))
        for start in range(0, len(order), self.batch_size):
            batch = [instances[i] for i in order[start : start + self.batch_size]]
            for (seq_index, _), result in zip(
                batch, self._predict_batch(sequences, batch, top_k), strict=True
            ):
                parts[seq_index].append(result)

        predictions = []
        for sequence_parts in parts:
            positions, true_tokens, ranks, top = (
                np.concatenate(x) for x in zip(*sequence_parts, strict=True)
            )
            order_in_seq = np.argsort(positions, kind="stable")
            predictions.append(
                MaskedPrediction(
                    positions=positions[order_in_seq],
                    true_tokens=true_tokens[order_in_seq],
                    ranks=ranks[order_in_seq],
                    top_tokens=top[order_in_seq],
                )
            )
        return predictions

    # Forward one batch of masked instances and compute, for every masked
    # position, the rank of the true key. Unseen keys (<unk>) can never be
    # predicted, so they get rank len(vocab) - always a miss.
    def _predict_batch(
        self,
        sequences: list[np.ndarray],
        batch: list[tuple[int, np.ndarray]],
        top_k: int,
    ) -> list[tuple[np.ndarray, ...]]:
        width = max(len(sequences[seq_index]) for seq_index, _ in batch)
        input_ids = torch.full((len(batch), width), PAD_INDEX, dtype=torch.long)
        targets = torch.full((len(batch), width), -1, dtype=torch.long)
        for row, (seq_index, positions) in enumerate(batch):
            sequence = torch.from_numpy(sequences[seq_index])
            input_ids[row, : len(sequence)] = sequence
            targets[row, positions] = sequence[positions]
            input_ids[row, positions] = MASK_INDEX

        logits = self.model(input_ids.to(self.device)).logits  # (batch, width, vocab)
        targets = targets.to(self.device)
        selected = targets >= 0
        masked_logits = logits[selected]  # (n_masked_total, vocab)
        true_tokens = targets[selected]
        true_scores = masked_logits.gather(1, true_tokens.unsqueeze(1))
        # Rank = how many keys the model scores strictly higher than the truth.
        ranks = (masked_logits > true_scores).sum(dim=1)
        ranks[true_tokens == UNK_INDEX] = len(self.vocab)
        k = min(top_k, masked_logits.size(1))
        top = masked_logits.topk(k, dim=1).indices if k > 0 else masked_logits[:, :0].long()

        # Split the flat results back into one tuple per instance (row order).
        counts = selected.sum(dim=1).tolist()
        ranks, true_tokens, top = ranks.cpu().numpy(), true_tokens.cpu().numpy(), top.cpu().numpy()
        results, offset = [], 0
        for (_, positions), count in zip(batch, counts, strict=True):
            span = slice(offset, offset + count)
            results.append(
                (np.asarray(positions), true_tokens[span], ranks[span].astype(np.int64), top[span])
            )
            offset += count
        return results

    # Score every window in `windows` (rows with start_idx / end_idx) using
    # the dataset's template-ID array. Returns ranks grouped per chunk/window.
    def score_windows(self, template_ids: np.ndarray, windows: pd.DataFrame) -> WindowScores:
        with Timer() as timer:
            sequences, owner = build_sequences(
                template_ids, windows, self.vocab, self.model.max_len, min_keys=1
            )
            predictions = self.predict_sequences(sequences)
        lengths = np.array([len(p.ranks) for p in predictions], dtype=np.int64)
        offsets = np.concatenate([[0], np.cumsum(lengths)[:-1]]) if len(lengths) else lengths
        ranks = (
            np.concatenate([p.ranks for p in predictions]) if predictions else np.empty(0, np.int64)
        )
        return WindowScores(
            ranks=ranks,
            chunk_offsets=offsets,
            chunk_window=owner,
            n_windows=len(windows),
            seconds=timer.seconds,
        )


# The (g, r) pair chosen on the validation period, its metrics there, and
# the full grid (saved as CSV for the report's sensitivity plot).
@dataclass
class ThresholdChoice:
    g: int
    r: int
    metrics: BinaryMetrics
    grid: pd.DataFrame


# Evaluate every (g, r) in g_values x {0..r_max} on labeled windows and pick
# the best F1. Ties go to fewer false positives, then smaller g and r. If the
# labels contain no anomalies at all, F1 is 0 everywhere and this rule
# falls back to the most conservative setting (fewest false positives).
def tune_thresholds(
    scores: WindowScores, labels: np.ndarray, g_values: list[int], r_max: int
) -> ThresholdChoice:
    labels = np.asarray(labels)
    rows = []
    for g in g_values:
        misses = scores.chunk_misses(g)
        for r in range(r_max + 1):
            metrics = binary_metrics(labels, scores.windows_from_chunks(misses > r))
            rows.append({"g": g, "r": r, **metrics.as_dict()})
    grid = pd.DataFrame(rows)
    best = grid.sort_values(["f1", "fp", "g", "r"], ascending=[False, True, True, True]).iloc[0]
    g, r = int(best["g"]), int(best["r"])
    return ThresholdChoice(
        g=g,
        r=r,
        metrics=binary_metrics(labels, scores.predict(g, r)),
        grid=grid,
    )
