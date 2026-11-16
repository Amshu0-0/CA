"""Error analysis of both LogBERT variants on the test windows.

Usage:
    python scripts/analyze.py
    python scripts/analyze.py --config configs/bgl_2k.yaml

Writes analysis/analysis.md (tables for the report) and one CSV per table to
the output directory.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from logad.config import add_config_arguments, config_from_args
from logad.data.processed import load_processed
from logad.evaluation.analysis import method_agreement, missed_alert_templates, per_category_recall
from logad.evaluation.compare import METHOD_LABELS, align_predictions, available_methods
from logad.evaluation.predictions import load_window_predictions
from logad.utils import ensure_dir, setup_logging


# Parse command-line arguments for this script.
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    add_config_arguments(parser)
    return parser.parse_args()


# Render a DataFrame as a Markdown table (floats with 3 decimals).
def to_markdown(frame: pd.DataFrame) -> str:
    if frame.empty:
        return "_(none)_"
    header = "| " + " | ".join(map(str, frame.columns)) + " |"
    divider = "|" + "---|" * len(frame.columns)
    rows = []
    for values in frame.itertuples(index=False):
        cells = [f"{v:.3f}" if isinstance(v, float) else str(v).replace("|", "\\|") for v in values]
        rows.append("| " + " | ".join(cells) + " |")
    return "\n".join([header, divider, *rows])


# Entry point: run every analysis and write the Markdown summary.
def main() -> None:
    setup_logging()
    config = config_from_args(parse_args())
    output_dir = Path(config.paths.output_dir)
    out = ensure_dir(output_dir / "analysis")
    logs = load_processed(
        config.paths.processed_dir,
        message_columns=["template_id", "label", "split", "alert_tag"],
    )
    test_windows = logs.windows_in("test")

    predictions = {
        method: align_predictions(
            load_window_predictions(output_dir, method), test_windows, method
        )["pred"].to_numpy()
        for method in available_methods(output_dir)
    }

    sections = ["# Error analysis (test period)\n"]
    if predictions:
        # 1. Recall per alert category.
        categories = per_category_recall(logs, predictions, "test")
        categories.to_csv(out / "per_category_recall.csv", index=False)
        sections += ["## Recall per alert category (window level)\n", to_markdown(categories), ""]

        # 2. Agreement between the two variants.
        agreement = method_agreement(test_windows["label"].to_numpy(), predictions)
        agreement.to_csv(out / "method_agreement.csv", index=False)
        sections += ["## Agreement on anomalous windows\n", to_markdown(agreement), ""]

        # 3. What each variant misses.
        sections.append("## Alert templates in missed windows\n")
        for method, preds in predictions.items():
            missed = missed_alert_templates(logs, preds, "test")
            missed.to_csv(out / f"missed_alerts_{method}.csv", index=False)
            sections += [f"### {METHOD_LABELS.get(method, method)}\n", to_markdown(missed), ""]

    (out / "analysis.md").write_text("\n".join(sections) + "\n", encoding="utf-8")
    print(f"Wrote {out / 'analysis.md'}")


if __name__ == "__main__":
    main()
