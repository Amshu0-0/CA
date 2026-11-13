"""Training LogBERT on normal windows from the training period.

Objective (LogBERT, Eq. 6):

    loss = MLKP + alpha * VHM

* MLKP: cross-entropy of the masked log keys (only masked positions count).
* VHM:  mean squared distance between each sequence's <dist> representation
        and the hypersphere centre ``c``. As in the LogBERT repository, ``c``
        is re-estimated at the start of every epoch as the mean <dist> output
        over (a sample of) the training sequences.

Training stops early when the loss on a held-out *dev* set stops improving.
The dev set is the chronologically last part of the normal training windows;
the labeled validation period is kept for tuning the detection settings.
"""

from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import torch
import torch.nn.functional as F
from torch.utils.data import DataLoader

from logad.config import LogBERTConfig, from_dict
from logad.data.processed import ProcessedLogs
from logad.logbert.dataset import (
    IGNORE_INDEX,
    LengthBucketSampler,
    MaskedSequenceDataset,
    build_sequences,
    collate_batch,
)
from logad.logbert.model import LogBERT
from logad.logbert.vocab import PAD_INDEX, LogKeyVocab
from logad.utils import Timer, ensure_dir, get_logger

logger = get_logger(__name__)


# Pick the compute device. "auto" prefers CUDA, then Apple's MPS, then CPU.
def resolve_device(name: str = "auto") -> torch.device:
    if name != "auto":
        return torch.device(name)
    if torch.cuda.is_available():
        return torch.device("cuda")
    if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


# Sequences and vocabulary that LogBERT is trained on.
@dataclass
class TrainingData:
    vocab: LogKeyVocab
    train_sequences: list[np.ndarray]
    dev_sequences: list[np.ndarray]


# Template IDs of every message covered by at least one of `windows`.
# Uses a difference array, so the cost is linear in the number of messages
# even though sliding windows overlap heavily.
def keys_covered_by(template_ids: np.ndarray, windows) -> np.ndarray:
    coverage = np.zeros(len(template_ids) + 1, dtype=np.int64)
    np.add.at(coverage, windows["start_idx"].to_numpy(), 1)
    np.add.at(coverage, windows["end_idx"].to_numpy(), -1)
    covered = np.cumsum(coverage[:-1]) > 0
    return np.unique(template_ids[covered])


# Keep the first copy of every distinct sequence, in their original order.
# Arrays are compared by their raw bytes, so only exact duplicates collapse.
def unique_sequences(sequences: list[np.ndarray]) -> list[np.ndarray]:
    seen: set[bytes] = set()
    unique = []
    for sequence in sequences:
        key = sequence.tobytes()
        if key not in seen:
            seen.add(key)
            unique.append(sequence)
    return unique


# Select LogBERT's training material from the processed dataset.
#
# 1. Keep only *normal* windows of the training period (LogBERT is trained
#    exclusively on normal behaviour).
# 2. Hold out the chronologically last `dev_fraction` of them for early stopping.
# 3. Build the vocabulary from all keys in normal training windows.
# 4. Turn windows into sequences, dropping ones shorter than `min_len` keys,
#    optionally drop exact duplicates and subsample the training sequences.
def prepare_training_data(logs: ProcessedLogs, config: LogBERTConfig, seed: int) -> TrainingData:
    windows = logs.windows_in("train")
    normal = windows[windows["label"] == 0].reset_index(drop=True)
    if normal.empty:
        raise ValueError("The training period contains no normal windows to train on")

    n_dev = int(round(len(normal) * config.dev_fraction))
    fit_windows = normal.iloc[: len(normal) - n_dev]
    dev_windows = normal.iloc[len(normal) - n_dev :]

    vocab = LogKeyVocab(keys_covered_by(logs.template_ids, normal))
    train_sequences, _ = build_sequences(
        logs.template_ids, fit_windows, vocab, config.max_len, min_keys=config.min_len
    )
    dev_sequences, _ = build_sequences(
        logs.template_ids, dev_windows, vocab, config.max_len, min_keys=config.min_len
    )

    if config.dedupe_sequences:
        train_sequences = unique_sequences(train_sequences)
        dev_sequences = unique_sequences(dev_sequences)

    if config.max_train_sequences and len(train_sequences) > config.max_train_sequences:
        rng = np.random.default_rng(seed)
        keep = np.sort(rng.choice(len(train_sequences), config.max_train_sequences, replace=False))
        train_sequences = [train_sequences[i] for i in keep]

    if not train_sequences:
        raise ValueError(
            f"No training sequences with at least min_len={config.min_len} keys; "
            "lower logbert.min_len for small datasets"
        )
    logger.info(
        "LogBERT data: %d normal train windows -> %d train / %d dev sequences, vocab %d keys",
        len(normal),
        len(train_sequences),
        len(dev_sequences),
        len(vocab.keys),
    )
    return TrainingData(vocab=vocab, train_sequences=train_sequences, dev_sequences=dev_sequences)


# Build a LogBERT network whose sizes come from the config.
def build_model(vocab_size: int, config: LogBERTConfig) -> LogBERT:
    return LogBERT(
        vocab_size=vocab_size,
        hidden=config.hidden,
        layers=config.layers,
        heads=config.heads,
        ff_dim=config.ff_dim,
        dropout=config.dropout,
        max_len=config.max_len,
    )


# Save everything needed to reuse a trained model: weights, the architecture
# arguments, the vocabulary, the hypersphere centre and the training history.
# Only plain Python types and tensors are stored, so the file loads with
# torch.load(weights_only=True).
def save_checkpoint(
    path: str | Path,
    model: LogBERT,
    vocab: LogKeyVocab,
    center: torch.Tensor,
    config: LogBERTConfig,
    history: list[dict[str, float]],
) -> None:
    path = Path(path)
    ensure_dir(path.parent)
    torch.save(
        {
            "model_state": model.state_dict(),
            "vocab": vocab.to_dict(),
            "center": center.detach().cpu(),
            "config": dataclasses.asdict(config),
            "history": history,
        },
        path,
    )


# Load a checkpoint written by `save_checkpoint` onto `device`.
# Returns (model in eval mode, vocabulary, centre, raw checkpoint dict).
def load_checkpoint(
    path: str | Path, device: torch.device | str = "cpu"
) -> tuple[LogBERT, LogKeyVocab, torch.Tensor, dict[str, Any]]:
    checkpoint = torch.load(path, map_location=device, weights_only=True)
    config = from_dict(LogBERTConfig, checkpoint["config"])
    vocab = LogKeyVocab.from_dict(checkpoint["vocab"])
    model = build_model(len(vocab), config).to(device)
    model.load_state_dict(checkpoint["model_state"])
    model.eval()
    return model, vocab, checkpoint["center"].to(device), checkpoint


# Runs the optimisation: one instance trains one model.
class LogBERTTrainer:
    def __init__(
        self, model: LogBERT, config: LogBERTConfig, device: torch.device, seed: int = 0
    ) -> None:
        self.model = model.to(device)
        self.config = config
        self.device = device
        self.seed = seed
        self.optimizer = torch.optim.AdamW(
            model.parameters(), lr=config.learning_rate, weight_decay=config.weight_decay
        )
        # Linear warm-up to the base learning rate, then constant.
        warmup = max(1, config.warmup_steps)
        self.scheduler = torch.optim.lr_scheduler.LambdaLR(
            self.optimizer, lambda step: min(1.0, (step + 1) / warmup)
        )
        # Hypersphere centre; set by compute_center() before each epoch.
        self.center = torch.zeros(config.hidden, device=device)

    # Create a DataLoader over masked versions of `sequences`.
    def _loader(
        self, sequences: list[np.ndarray], deterministic: bool, shuffle: bool
    ) -> DataLoader:
        dataset = MaskedSequenceDataset(
            sequences,
            vocab_size=self.model.mlkp_head.out_features,
            mask_ratio=self.config.mask_ratio,
            deterministic=deterministic,
            seed=self.seed,
        )
        sampler = LengthBucketSampler(
            [len(s) for s in sequences], self.config.batch_size, shuffle=shuffle, seed=self.seed
        )
        return DataLoader(dataset, batch_sampler=sampler, collate_fn=collate_batch)

    # Estimate the hypersphere centre c as the mean <dist> output over up to
    # `center_samples` training sequences, run unmasked in eval mode.
    @torch.no_grad()
    def compute_center(self, sequences: list[np.ndarray]) -> torch.Tensor:
        rng = np.random.default_rng(self.seed)
        count = min(len(sequences), self.config.center_samples)
        chosen = rng.choice(len(sequences), count, replace=False)
        sample = [sequences[i] for i in chosen]

        self.model.eval()
        total = torch.zeros(self.config.hidden, device=self.device)
        order = np.argsort([len(s) for s in sample])  # similar lengths -> less padding
        for start in range(0, count, self.config.batch_size):
            batch = [sample[i] for i in order[start : start + self.config.batch_size]]
            input_ids = self._pad(batch)
            total += self.model(input_ids).dist.sum(dim=0)
        return total / count

    # Pad raw sequences into one (batch, max_len) tensor on the device.
    def _pad(self, sequences: list[np.ndarray]) -> torch.Tensor:
        width = max(len(s) for s in sequences)
        input_ids = torch.full((len(sequences), width), PAD_INDEX, dtype=torch.long)
        for row, sequence in enumerate(sequences):
            input_ids[row, : len(sequence)] = torch.from_numpy(sequence)
        return input_ids.to(self.device)

    # Compute (total, mlkp, vhm) losses for one collated batch.
    def _losses(self, batch: dict[str, torch.Tensor]) -> tuple[torch.Tensor, ...]:
        batch = {key: value.to(self.device) for key, value in batch.items()}
        output = self.model(batch["input_ids"], batch["attention_mask"])
        # Cross-entropy over masked positions only (others are IGNORE_INDEX).
        mlkp = F.cross_entropy(
            output.logits.reshape(-1, output.logits.size(-1)),
            batch["labels"].reshape(-1),
            ignore_index=IGNORE_INDEX,
        )
        # Mean squared distance to the centre, as nn.MSELoss in the LogBERT repo.
        vhm = F.mse_loss(output.dist, self.center.expand_as(output.dist))
        return mlkp + self.config.vhm_weight * vhm, mlkp, vhm

    # One pass over the training data. Returns average losses.
    def train_epoch(self, loader: DataLoader) -> dict[str, float]:
        self.model.train()
        sums = np.zeros(3)
        for batch in loader:
            loss, mlkp, vhm = self._losses(batch)
            self.optimizer.zero_grad(set_to_none=True)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(self.model.parameters(), self.config.grad_clip)
            self.optimizer.step()
            self.scheduler.step()
            sums += [loss.item(), mlkp.item(), vhm.item()]
        return self._averages(sums, len(loader))

    # Turn summed (loss, mlkp, vhm) into per-batch averages as plain Python
    # floats (NumPy scalars would break torch.load(weights_only=True)).
    @staticmethod
    def _averages(sums: np.ndarray, n_batches: int) -> dict[str, float]:
        n = max(1, n_batches)
        names = ("loss", "mlkp", "vhm")
        return {name: float(total / n) for name, total in zip(names, sums, strict=True)}

    # Average losses on `loader` without updating the model.
    @torch.no_grad()
    def evaluate(self, loader: DataLoader) -> dict[str, float]:
        self.model.eval()
        sums = np.zeros(3)
        for batch in loader:
            sums += [value.item() for value in self._losses(batch)]
        return self._averages(sums, len(loader))

    # Train until the dev loss stops improving for `patience` epochs (or the
    # epoch budget runs out). The best model is written to `checkpoint_path`.
    # Returns the per-epoch history (losses, learning rate, seconds).
    def fit(self, data: TrainingData, checkpoint_path: str | Path) -> list[dict[str, float]]:
        train_loader = self._loader(data.train_sequences, deterministic=False, shuffle=True)
        dev_loader = (
            self._loader(data.dev_sequences, deterministic=True, shuffle=False)
            if data.dev_sequences
            else None
        )
        if dev_loader is None:
            logger.warning("No dev sequences; early stopping will monitor the training loss")

        history: list[dict[str, float]] = []
        best_loss, epochs_without_improvement = math.inf, 0
        for epoch in range(1, self.config.epochs + 1):
            with Timer() as timer:
                self.center = self.compute_center(data.train_sequences)
                train = self.train_epoch(train_loader)
                dev = self.evaluate(dev_loader) if dev_loader is not None else train
            record = {
                "epoch": epoch,
                **{f"train_{k}": v for k, v in train.items()},
                **{f"dev_{k}": v for k, v in dev.items()},
                "lr": self.optimizer.param_groups[0]["lr"],
                "seconds": timer.seconds,
            }
            history.append(record)
            logger.info(
                "epoch %3d | train %.4f (mlkp %.4f, vhm %.4f) | dev %.4f | %.1fs",
                epoch,
                train["loss"],
                train["mlkp"],
                train["vhm"],
                dev["loss"],
                timer.seconds,
            )

            if dev["loss"] < best_loss - 1e-6:
                best_loss, epochs_without_improvement = dev["loss"], 0
                save_checkpoint(
                    checkpoint_path, self.model, data.vocab, self.center, self.config, history
                )
            else:
                epochs_without_improvement += 1
                if epochs_without_improvement >= self.config.patience:
                    logger.info(
                        "Early stopping after epoch %d (best dev loss %.4f)", epoch, best_loss
                    )
                    break
        return history
