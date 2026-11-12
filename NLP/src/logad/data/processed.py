"""On-disk format of the preprocessed dataset, and a loader for it.

``scripts/preprocess.py`` writes one directory per dataset::

    data/processed/<name>/
        messages.parquet   one row per log message, in chronological order
        templates.csv      one row per Drain template, with per-split label counts
        windows.parquet    one row per sliding window: [start_idx, end_idx) into messages
        drain.pkl          the fitted TemplateExtractor (lets the demo parse new lines)
        stats.json         dataset statistics quoted in the report

Every later stage (LogBERT, rule induction, LLM prompting, evaluation, demo)
starts from ``load_processed``, so all methods see identical data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from logad.utils import ensure_dir, load_json, save_json

MESSAGES_FILE = "messages.parquet"
TEMPLATES_FILE = "templates.csv"
WINDOWS_FILE = "windows.parquet"
DRAIN_FILE = "drain.pkl"
STATS_FILE = "stats.json"


# In-memory view of one preprocessed dataset.
#
#   messages   chronological messages with template_id, label and split
#   templates  template_id -> template text and label statistics
#   windows    sliding windows with their split and ground-truth label
#   stats      summary numbers (sizes, anomaly rates, template purity, ...)
@dataclass
class ProcessedLogs:
    messages: pd.DataFrame
    templates: pd.DataFrame
    windows: pd.DataFrame
    stats: dict[str, Any] = field(default_factory=dict)

    # Template ID of every message as a NumPy array; slicing this array is how
    # a window's log-key sequence is built, so it is computed once and cached.
    @cached_property
    def template_ids(self) -> np.ndarray:
        return self.messages["template_id"].to_numpy(dtype=np.int64)

    # Ground-truth label (0/1) of every message, aligned with `messages`.
    @cached_property
    def message_labels(self) -> np.ndarray:
        return self.messages["label"].to_numpy(dtype=np.int8)

    # Mapping template_id -> template text.
    @cached_property
    def template_text(self) -> dict[int, str]:
        return dict(zip(self.templates["template_id"], self.templates["template"], strict=True))

    # All windows of one split ("train", "val" or "test"), in time order.
    def windows_in(self, split: str) -> pd.DataFrame:
        return self.windows[self.windows["split"] == split].reset_index(drop=True)

    # The template-ID sequence (log keys) of the window [start_idx, end_idx).
    def window_sequence(self, start_idx: int, end_idx: int) -> np.ndarray:
        return self.template_ids[start_idx:end_idx]


# Write all tables of `logs` into `directory` (created if necessary).
def save_processed(logs: ProcessedLogs, directory: str | Path) -> Path:
    directory = ensure_dir(directory)
    logs.messages.to_parquet(directory / MESSAGES_FILE, index=False)
    logs.templates.to_csv(directory / TEMPLATES_FILE, index=False)
    logs.windows.to_parquet(directory / WINDOWS_FILE, index=False)
    save_json(logs.stats, directory / STATS_FILE)
    return directory


# Load only the template table (rule induction needs nothing else).
def load_templates(directory: str | Path) -> pd.DataFrame:
    path = Path(directory) / TEMPLATES_FILE
    if not path.exists():
        raise FileNotFoundError(f"{path} not found. Run `python scripts/preprocess.py` first.")
    # keep_default_na=False: a template is free text and must never become NaN.
    return pd.read_csv(path, keep_default_na=False)


# Load a dataset written by `save_processed`.
#
# `message_columns` restricts which message columns are read; LogBERT, for
# example, only needs template IDs, labels and splits, which keeps memory low
# on the 4.7M-message BGL file.
def load_processed(
    directory: str | Path, message_columns: list[str] | None = None
) -> ProcessedLogs:
    directory = Path(directory)
    if not (directory / MESSAGES_FILE).exists():
        raise FileNotFoundError(
            f"No processed dataset in {directory}. Run `python scripts/preprocess.py` first."
        )
    messages = pd.read_parquet(directory / MESSAGES_FILE, columns=message_columns)
    # keep_default_na=False: a template is free text and must never become NaN.
    templates = pd.read_csv(directory / TEMPLATES_FILE, keep_default_na=False)
    windows = pd.read_parquet(directory / WINDOWS_FILE)
    stats_path = directory / STATS_FILE
    stats = load_json(stats_path) if stats_path.exists() else {}
    return ProcessedLogs(messages=messages, templates=templates, windows=windows, stats=stats)
