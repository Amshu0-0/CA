"""Preprocess a raw BGL log into templates, splits and sliding windows.

Usage:
    python scripts/preprocess.py                              # full BGL (configs/bgl.yaml)
    python scripts/preprocess.py --config configs/bgl_2k.yaml # 2k-line smoke test
"""

from __future__ import annotations

import argparse

from logad.config import add_config_arguments, config_from_args
from logad.data.preprocess import preprocess
from logad.utils import setup_logging


# Parse command-line arguments for this script.
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    add_config_arguments(parser)
    parser.add_argument(
        "--max-lines", type=int, default=None, help="Only read the first N raw lines"
    )
    return parser.parse_args()


# Print the per-split statistics as a small aligned table.
def print_summary(stats: dict) -> None:
    print(
        f"\n{stats['n_messages']:,} messages | {stats['n_anomalous_messages']:,} alerts "
        f"({100 * stats['anomaly_rate']:.2f}%) | {stats['n_templates']} templates"
    )
    print(
        f"{'split':<6} {'messages':>10} {'alerts':>9} {'windows':>9} {'anom.win':>9} {'new tpl':>8}"
    )
    for split, s in stats["splits"].items():
        print(
            f"{split:<6} {s['n_messages']:>10,} {s['n_anomalous_messages']:>9,} "
            f"{s['n_windows']:>9,} {s['n_anomalous_windows']:>9,} "
            f"{s['n_templates_unseen_in_train']:>8}"
        )
    purity = stats["template_purity"]
    print(
        f"mixed-label templates: {purity['n_mixed_templates']} "
        f"({100 * purity['share_of_messages_in_mixed_templates']:.2f}% of messages)\n"
    )


# Entry point: run preprocessing and print a summary.
def main() -> None:
    setup_logging()
    args = parse_args()
    config = config_from_args(args)
    logs = preprocess(config, max_lines=args.max_lines)
    print_summary(logs.stats)


if __name__ == "__main__":
    main()
