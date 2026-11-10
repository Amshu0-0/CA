"""Small helpers shared across the project: logging, seeding, timing and JSON I/O."""

from __future__ import annotations

import json
import logging
import os
import random
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

_LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"


# Configure the root logger once, with a timestamped format, so that every
# module's `get_logger(__name__)` output looks the same in the console.
def setup_logging(level: str | int = "INFO") -> None:
    logging.basicConfig(level=level, format=_LOG_FORMAT, datefmt="%H:%M:%S", stream=sys.stderr)


# Return a module-level logger; a thin wrapper kept for a single import site.
def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


# Seed every random number generator the project uses (Python, NumPy and, if
# it is installed, PyTorch) so that runs are reproducible.
def set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    os.environ["PYTHONHASHSEED"] = str(seed)
    try:
        import torch
    except ImportError:  # PyTorch is only needed for the LogBERT stages.
        return
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


# Context manager that measures wall-clock time of the block it wraps.
#
#     with Timer() as t:
#         run_inference()
#     print(t.seconds)
#
# Used to report the inference time of every method in the comparison.
class Timer:
    def __init__(self) -> None:
        self.start: float | None = None
        self.seconds: float = 0.0

    def __enter__(self) -> Timer:
        # perf_counter is monotonic and has the highest available resolution.
        self.start = time.perf_counter()
        return self

    def __exit__(self, *exc_info: object) -> None:
        assert self.start is not None
        self.seconds = time.perf_counter() - self.start


# Create `path` (and parents) if needed and return it as a Path.
def ensure_dir(path: str | Path) -> Path:
    directory = Path(path)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


# Convert NumPy scalars/arrays and Paths to JSON-friendly Python types.
# Passed as `default=` to json.dump, which calls it for unknown objects.
def _json_default(obj: Any) -> Any:
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        return float(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, Path):
        return str(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


# Write `obj` as pretty-printed UTF-8 JSON, creating the parent directory.
def save_json(obj: Any, path: str | Path) -> None:
    path = Path(path)
    ensure_dir(path.parent)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, ensure_ascii=False, default=_json_default)
        fh.write("\n")


# Read a JSON file written by `save_json` (or any other UTF-8 JSON file).
def load_json(path: str | Path) -> Any:
    with Path(path).open("r", encoding="utf-8") as fh:
        return json.load(fh)
