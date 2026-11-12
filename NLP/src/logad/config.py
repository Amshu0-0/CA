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


# Drain log-parser settings (He et al., 2017). The defaults reproduce the
# LogBERT repository's preprocessing of BGL.
@dataclass
class ParsingConfig:
    # Depth of Drain's parse tree; 3 groups messages by token count only.
    depth: int = 3
    # Minimum fraction of matching tokens for a message to join a template.
    similarity_threshold: float = 0.3
    # Maximum number of children of an internal tree node.
    max_children: int = 100
    # Regexes replaced by <*> before parsing: hex numbers, IPv4 addresses,
    # integers (same list as logad.data.drain.DEFAULT_MASKING).
    masking: list[str] = field(
        default_factory=lambda: [r"(0x)[0-9a-fA-F]+", r"\d+\.\d+\.\d+\.\d+", r"\d+"]
    )


# Chronological train / validation / test split (Le & Zhang, ICSE 2022).
@dataclass
class SplitConfig:
    # "time" cuts the timeline into fractions; "messages" cuts the message count.
    by: str = "time"
    train: float = 0.6
    val: float = 0.2
    test: float = 0.2


# Sliding windows: the shared evaluation unit of both approaches
# (LogBERT's BGL setting: 5-minute windows moved forward by 1 minute).
@dataclass
class WindowConfig:
    size_seconds: int = 300
    step_seconds: int = 60


# How LogBERT turns masked-key predictions into window labels, and the grid
# searched on the validation period to pick the two detection settings.
@dataclass
class LogBERTDetectionConfig:
    # Fraction of keys masked in each test sequence.
    mask_ratio: float = 0.5
    # Extension: mask every key exactly once (in ceil(1/mask_ratio) passes)
    # instead of LogBERT's single random mask. Saved as method "logbert_full".
    full_coverage: bool = False
    # Candidate set sizes g: a key is "normal" if it is in the top-g predictions.
    g_values: list[int] = field(default_factory=lambda: [1, 2, 3, 4, 5, 6, 8, 10, 12, 15, 20])
    # Anomalous-key counts r tried: 0, 1, ..., r_max. A sequence is anomalous
    # if more than r of its masked keys fall outside the top-g candidates.
    r_max: int = 20
    # Batch size for scoring (no gradients, so it can be larger than training).
    batch_size: int = 128
    # Seed for choosing which keys to mask, so scores are reproducible.
    seed: int = 1234


# Approach A: LogBERT architecture, training and detection settings.
@dataclass
class LogBERTConfig:
    # --- architecture (two-layer encoder as described in the LogBERT paper) ---
    hidden: int = 256
    layers: int = 2
    heads: int = 4
    ff_dim: int = 1024
    dropout: float = 0.1
    # Longest sequence incl. the <dist> token; longer windows are chunked.
    max_len: int = 512
    # --- training data ---
    # Skip training windows with fewer keys than this (LogBERT's min_len).
    min_len: int = 10
    # Chronologically last fraction of normal training windows held out for
    # early stopping (the labeled validation period is used for g / r only).
    dev_fraction: float = 0.1
    # Randomly subsample training sequences (e.g. for quick CPU runs); null = all.
    max_train_sequences: int | None = None
    # --- objective ---
    # Fraction of keys masked for masked log-key prediction (MLKP).
    mask_ratio: float = 0.5
    # Weight alpha of the hypersphere (VHM) loss: loss = MLKP + alpha * VHM.
    vhm_weight: float = 0.1
    # Number of training sequences used to estimate the hypersphere centre.
    center_samples: int = 20000
    # --- optimisation ---
    batch_size: int = 64
    learning_rate: float = 1.0e-3
    weight_decay: float = 0.0
    warmup_steps: int = 500
    grad_clip: float = 1.0
    epochs: int = 50
    # Stop after this many epochs without dev-loss improvement.
    patience: int = 5
    # "auto" picks CUDA, then Apple MPS, then CPU.
    device: str = "auto"
    detection: LogBERTDetectionConfig = field(default_factory=LogBERTDetectionConfig)


# Connection settings for one LLM. Two are configured: the rule *generator*
# (GPT-4o-mini in LogRules) and the *classifier* (Qwen2.5-7B-Instruct).
@dataclass
class LLMBackendConfig:
    # "openai": the OpenAI API or any OpenAI-compatible server (Ollama, vLLM,
    # LM Studio, ...); "transformers": a Hugging Face model loaded locally.
    backend: str = "openai"
    model: str = "gpt-4o-mini"
    # Server URL for OpenAI-compatible backends, e.g. http://localhost:11434/v1
    # for Ollama; null means the official OpenAI API.
    base_url: str | None = None
    # Environment variable that holds the API key (local servers need none).
    api_key_env: str = "OPENAI_API_KEY"
    # 0 = greedy decoding, so results are as reproducible as the backend allows.
    temperature: float = 0.0
    max_tokens: int = 512
    timeout_seconds: float = 120.0
    # Ask the server for JSON output (response_format=json_object) if supported.
    json_mode: bool = False
    # transformers backend only: load weights in 4 bit to fit a 16 GB GPU.
    load_in_4bit: bool = False
    seed: int = 0


# Settings shared by everything that calls an LLM.
@dataclass
class LLMConfig:
    # Every prompt and raw response is cached here (JSONL), which makes reruns
    # free and keeps a complete, auditable record of all LLM calls.
    cache_dir: str = "outputs/llm_cache"
    generator: LLMBackendConfig = field(default_factory=LLMBackendConfig)
    classifier: LLMBackendConfig = field(default_factory=LLMBackendConfig)


# LogRules-style rule induction (Approach B).
@dataclass
class RulesConfig:
    # Labeled training templates shown per induction prompt (half abnormal,
    # half normal when possible).
    batch_size: int = 20
    # Passes over the training templates, each with a different shuffle.
    rounds: int = 3
    # Maximum number of rules the generator may propose per prompt.
    max_rules_per_batch: int = 8
    # Keep a rule only if it labels at least this share of the training
    # templates it matches correctly (1.0 = never wrong on training data)...
    min_precision: float = 1.0
    # ...and matches at least this many training templates.
    min_support: int = 1
    # Number of top-ranked rules used by the rule-based prompt and the
    # keyword baseline (both use the same rules, for a fair comparison).
    top_k: int = 30


# Root of the configuration tree: one instance describes one experiment.
@dataclass
class ExperimentConfig:
    # Short name used in log messages and result tables.
    name: str = "bgl"
    # Global random seed (Python, NumPy and PyTorch are all seeded from it).
    seed: int = 42
    paths: PathsConfig = field(default_factory=PathsConfig)
    parsing: ParsingConfig = field(default_factory=ParsingConfig)
    split: SplitConfig = field(default_factory=SplitConfig)
    windows: WindowConfig = field(default_factory=WindowConfig)
    logbert: LogBERTConfig = field(default_factory=LogBERTConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    rules: RulesConfig = field(default_factory=RulesConfig)


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
