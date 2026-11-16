"""Building the results table from the saved predictions of every LogBERT variant.

Metrics are **recomputed** here from each variant's prediction file against the
single source of truth for labels (``windows.parquet``), and every variant must
cover exactly the same test windows. This guards the core claim of the
project: all numbers come from one shared evaluation unit, the test window.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from logad.evaluation.metrics import binary_metrics
from logad.evaluation.predictions import (
    PREDICTIONS_DIR,
    load_method_results,
    load_window_predictions,
)

# Display order and names of the known methods (unknown ones are appended).
# A is LogBERT as published (about half of the keys of a window are hidden at
# random); A+ is our extension that hides every key exactly once.
METHOD_LABELS: dict[str, str] = {
    "logbert": "A  LogBERT",
    "logbert_full": "A+ LogBERT, full-coverage masking (ext.)",
}


# Names of all methods with a prediction file, in display order.
def available_methods(output_dir: str | Path) -> list[str]:
    directory = Path(output_dir) / PREDICTIONS_DIR
    found = sorted(p.stem for p in directory.glob("*.parquet")) if directory.exists() else []
    known = [m for m in METHOD_LABELS if m in found]
    return known + [m for m in found if m not in METHOD_LABELS]


# Join a method's predictions to the test windows, refusing partial coverage.
def align_predictions(predictions: pd.DataFrame, test_windows: pd.DataFrame, method: str):
    merged = test_windows[["window_id", "label"]].merge(
        predictions, on="window_id", how="left", validate="one_to_one"
    )
    missing = int(merged["pred"].isna().sum())
    extra = len(set(predictions["window_id"]) - set(test_windows["window_id"]))
    if missing or extra:
        raise ValueError(
            f"{method}: predictions do not match the shared test windows "
            f"({missing} missing, {extra} unexpected). Re-run the method on this dataset."
        )
    return merged


# One row per method: window-level metrics recomputed from predictions, plus
# the inference time from its result file.
def comparison_table(output_dir: str | Path, test_windows: pd.DataFrame) -> pd.DataFrame:
    results = load_method_results(output_dir)
    rows = []
    for method in available_methods(output_dir):
        merged = align_predictions(
            load_window_predictions(output_dir, method), test_windows, method
        )
        metrics = binary_metrics(merged["label"].to_numpy(), merged["pred"].to_numpy())
        result = results.get(method, {})
        seconds = result.get("inference_seconds")
        rows.append(
            {
                "method": method,
                "name": METHOD_LABELS.get(method, method),
                **metrics.as_dict(),
                "inference_seconds": seconds,
                "ms_per_window": 1000 * seconds / len(test_windows) if seconds else None,
            }
        )
    return pd.DataFrame(rows)


# Render the comparison as Markdown (window-level metrics first).
def comparison_markdown(table: pd.DataFrame, n_windows: int, n_anomalous: int) -> str:
    lines = [
        f"Test set: {n_windows:,} windows, {n_anomalous:,} anomalous "
        f"({100 * n_anomalous / max(1, n_windows):.1f}%).",
        "",
        "| Method | Precision | Recall | F1 | FP | FN | Inference (s) |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in table.itertuples():
        seconds = f"{row.inference_seconds:.1f}" if pd.notna(row.inference_seconds) else "-"
        lines.append(
            f"| {row.name} | {row.precision:.3f} | {row.recall:.3f} | {row.f1:.3f} | "
            f"{row.fp:,} | {row.fn:,} | {seconds} |"
        )
    return "\n".join(lines)
