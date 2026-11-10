"""Typed experiment configuration loaded from YAML.

Every tunable setting of the project (file paths, parser settings, window
sizes, model hyper-parameters, LLM settings, ...) lives in a YAML file under
``configs/``. This module turns that YAML into nested dataclasses so that

* every option has a documented default in exactly one place,
* a typo in a key fails loudly instead of being silently ignored, and
* scripts can override single values from the command line, e.g.
  ``--set logbert.epochs=2`` for a quick smoke run.

A config file may inherit from another one with ``extends: other.yaml``; the
child file then only needs to list the values it changes.
"""

from __future__ import annotations

import argparse
import copy
import dataclasses
import typing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# ---------------------------------------------------------------------------
# Config sections
# ---------------------------------------------------------------------------


# Where the input log lives and where every stage writes its results.
# Relative paths are resolved against the directory the scripts are run from
# (the repository root in all documented commands).
@dataclass
class PathsConfig:
    # Raw Loghub BGL file, one log message per line.
    raw_log: str = "data/raw/BGL.log"
    # Parsed messages, templates and windows written by scripts/preprocess.py.
    processed_dir: str = "data/processed/bgl"
    # Checkpoints, predictions and metrics of every method.
    output_dir: str = "outputs/bgl"


# Root of the configuration tree: one instance describes one experiment.
@dataclass
class ExperimentConfig:
    # Short name used in log messages and result tables.
    name: str = "bgl"
    # Global random seed (Python, NumPy and PyTorch are all seeded from it).
    seed: int = 42
    paths: PathsConfig = field(default_factory=PathsConfig)


# ---------------------------------------------------------------------------
# Loading helpers
# ---------------------------------------------------------------------------


# Recursively merge `override` into a deep copy of `base` and return the result.
# Nested dicts are merged key by key; any other value in `override` replaces
# the value in `base` outright (lists are replaced, not concatenated).
def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


# Read a YAML file and resolve its optional `extends:` parent chain.
# The parent path is interpreted relative to the child file, so configs can
# reference each other no matter where the scripts are launched from.
def _read_yaml_with_extends(path: Path, _seen: set[Path] | None = None) -> dict[str, Any]:
    seen = set() if _seen is None else _seen
    path = path.resolve()
    if path in seen:
        raise ValueError(f"Circular 'extends' chain detected at {path}")
    seen.add(path)

    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise TypeError(f"Top level of {path} must be a mapping, got {type(data).__name__}")

    parent = data.pop("extends", None)
    if parent is None:
        return data
    base = _read_yaml_with_extends(path.parent / parent, seen)
    return deep_merge(base, data)


# Turn one command-line override such as "logbert.epochs=2" into a nested dict
# ({"logbert": {"epochs": 2}}). The value is parsed as YAML, so "2" becomes an
# int, "true" a bool, "null" None and "[1, 2]" a list.
def parse_override(expression: str) -> dict[str, Any]:
    if "=" not in expression:
        raise ValueError(f"Override must look like 'section.key=value', got {expression!r}")
    dotted_key, raw_value = expression.split("=", 1)
    keys = [k for k in dotted_key.strip().split(".") if k]
    if not keys:
        raise ValueError(f"Override has an empty key: {expression!r}")

    nested: Any = yaml.safe_load(raw_value)
    # Build the dict from the innermost key outwards.
    for key in reversed(keys):
        nested = {key: nested}
    return nested


# Build dataclass `cls` from a plain dict, recursing into nested sections.
# Unknown keys raise a KeyError that names the offending section, which turns
# config typos into immediate, readable errors.
def from_dict(cls: type, data: dict[str, Any] | None) -> Any:
    if data is None:
        return cls()
    if not isinstance(data, dict):
        raise TypeError(f"Expected a mapping for {cls.__name__}, got {type(data).__name__}")

    known = {f.name for f in dataclasses.fields(cls)}
    unknown = sorted(set(data) - known)
    if unknown:
        raise KeyError(f"Unknown key(s) for {cls.__name__}: {unknown}. Valid keys: {sorted(known)}")

    # get_type_hints resolves the string annotations produced by
    # `from __future__ import annotations` back into real classes.
    hints = typing.get_type_hints(cls)
    kwargs: dict[str, Any] = {}
    for f in dataclasses.fields(cls):
        if f.name not in data:
            continue  # keep the dataclass default
        value = data[f.name]
        field_type = hints[f.name]
        if dataclasses.is_dataclass(field_type) and isinstance(field_type, type):
            value = from_dict(field_type, value)
        kwargs[f.name] = value
    return cls(**kwargs)


# Load an experiment configuration from `path`, apply command-line overrides
# in order, and return a fully-typed ExperimentConfig.
def load_config(path: str | Path, overrides: list[str] | tuple[str, ...] = ()) -> ExperimentConfig:
    data = _read_yaml_with_extends(Path(path))
    for expression in overrides:
        data = deep_merge(data, parse_override(expression))
    return from_dict(ExperimentConfig, data)


# Convert a config back into plain Python types so it can be saved next to
# every experiment's outputs (useful for reproducing a run later).
def config_to_dict(config: ExperimentConfig) -> dict[str, Any]:
    return dataclasses.asdict(config)


# Register the standard `--config` / `--set` options on a script's parser so
# that every entry point in scripts/ is configured the same way.
def add_config_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--config",
        default="configs/bgl.yaml",
        help="Path to the experiment YAML file (default: %(default)s).",
    )
    parser.add_argument(
        "--set",
        dest="overrides",
        action="append",
        default=[],
        metavar="SECTION.KEY=VALUE",
        help="Override a single config value; may be repeated, e.g. --set logbert.epochs=2",
    )


# Load the config described by parsed `--config` / `--set` arguments.
def config_from_args(args: argparse.Namespace) -> ExperimentConfig:
    return load_config(args.config, args.overrides)
