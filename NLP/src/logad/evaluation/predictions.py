"""Common on-disk format for every method's predictions and results.

Each method writes to the experiment's output directory::

    predictions/<method>.parquet   one row per test window:
                                     window_id (matches windows.parquet),
                                     pred (0/1), score (higher = more anomalous, may be NaN)
    results/<method>.json          metrics, chosen settings and inference time

``scripts/compare.py`` reads these files to build the final comparison, so a
new method only has to write the same two files to join the table.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from logad.utils import ensure_dir, load_json, save_json

PREDICTIONS_DIR = "predictions"
RESULTS_DIR = "results"


# Write a method's window-level predictions. `scores` is optional; methods
# without a continuous score store NaN.
def save_window_predictions(
    output_dir: str | Path,
    method: str,
    window_ids: np.ndarray,
    preds: np.ndarray,
    scores: np.ndarray | None = None,
) -> Path:
    frame = pd.DataFrame(
        {
            "window_id": np.asarray(window_ids, dtype=np.int64),
            "pred": np.asarray(preds, dtype=np.int8),
            "score": np.full(len(preds), np.nan) if scores is None else np.asarray(scores, float),
        }
    )
    path = ensure_dir(Path(output_dir) / PREDICTIONS_DIR) / f"{method}.parquet"
    frame.to_parquet(path, index=False)
    return path


# Read predictions written by `save_window_predictions`.
def load_window_predictions(output_dir: str | Path, method: str) -> pd.DataFrame:
    return pd.read_parquet(Path(output_dir) / PREDICTIONS_DIR / f"{method}.parquet")


# Write a method's result summary (metrics, settings, timing) as JSON.
def save_method_result(output_dir: str | Path, method: str, result: dict[str, Any]) -> Path:
    path = Path(output_dir) / RESULTS_DIR / f"{method}.json"
    save_json({"method": method, **result}, path)
    return path


# Read every result summary in the output directory, keyed by method name.
def load_method_results(output_dir: str | Path) -> dict[str, dict[str, Any]]:
    directory = Path(output_dir) / RESULTS_DIR
    if not directory.exists():
        return {}
    return {path.stem: load_json(path) for path in sorted(directory.glob("*.json"))}
