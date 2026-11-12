"""Compare every evaluated method on the shared test windows.

Usage:
    python scripts/compare.py
    python scripts/compare.py --config configs/bgl_2k.yaml

Reads predictions/*.parquet and results/*.json from the output directory and
writes results/comparison.md and results/comparison.csv.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from logad.config import add_config_arguments, config_from_args
from logad.evaluation.compare import comparison_markdown, comparison_table
from logad.evaluation.predictions import RESULTS_DIR
from logad.utils import ensure_dir, setup_logging


# Parse command-line arguments for this script.
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    add_config_arguments(parser)
    return parser.parse_args()


# Entry point: build, print and save the comparison table.
def main() -> None:
    setup_logging()
    config = config_from_args(parse_args())
    output_dir = Path(config.paths.output_dir)

    windows = pd.read_parquet(Path(config.paths.processed_dir) / "windows.parquet")
    test_windows = windows[windows["split"] == "test"].reset_index(drop=True)
    table = comparison_table(output_dir, test_windows)
    if table.empty:
        print(f"No predictions found in {output_dir}. Run the evaluation scripts first.")
        return

    markdown = comparison_markdown(table, len(test_windows), int(test_windows["label"].sum()))
    results_dir = ensure_dir(output_dir / RESULTS_DIR)
    (results_dir / "comparison.md").write_text(markdown + "\n", encoding="utf-8")
    table.to_csv(results_dir / "comparison.csv", index=False)
    print(markdown)
    print(f"\nSaved to {results_dir / 'comparison.md'}")


if __name__ == "__main__":
    main()
