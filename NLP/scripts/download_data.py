"""Download the BGL dataset from Loghub into data/raw/.

Usage:
    python scripts/download_data.py                # full BGL (~700 MB unpacked)
    python scripts/download_data.py --dataset bgl_2k   # 2,000-line sample for smoke tests
"""

from __future__ import annotations

import argparse

from logad.data.download import SOURCES, download_dataset
from logad.utils import setup_logging


# Parse command-line arguments for this script.
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dataset", choices=sorted(SOURCES), default="bgl")
    parser.add_argument("--output-dir", default="data/raw")
    parser.add_argument("--force", action="store_true", help="Re-download even if the file exists")
    return parser.parse_args()


# Entry point: download the requested dataset and print where it was saved.
def main() -> None:
    setup_logging()
    args = parse_args()
    path = download_dataset(args.dataset, args.output_dir, force=args.force)
    print(path)


if __name__ == "__main__":
    main()
