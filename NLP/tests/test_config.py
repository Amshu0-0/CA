"""Tests for the YAML configuration loader."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from logad.config import ExperimentConfig, deep_merge, load_config, parse_override


# Helper: write `text` (dedented) to `path` and return the path.
def _write(path: Path, text: str) -> Path:
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    return path


# The checked-in default config must always load without errors.
def test_default_config_loads() -> None:
    config = load_config("configs/bgl.yaml")
    assert isinstance(config, ExperimentConfig)
    assert config.name == "bgl"
    assert config.paths.raw_log.endswith("BGL.log")


# Nested dicts merge key by key while scalars and lists are replaced.
def test_deep_merge_merges_nested_dicts() -> None:
    base = {"a": {"x": 1, "y": 2}, "b": [1, 2]}
    merged = deep_merge(base, {"a": {"y": 3}, "b": [9]})
    assert merged == {"a": {"x": 1, "y": 3}, "b": [9]}
    assert base == {"a": {"x": 1, "y": 2}, "b": [1, 2]}  # input left untouched


# Override values are parsed as YAML, so types come out right.
@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("seed=7", {"seed": 7}),
        ("paths.raw_log=foo.log", {"paths": {"raw_log": "foo.log"}}),
        ("a.b.c=true", {"a": {"b": {"c": True}}}),
        ("x=[1, 2]", {"x": [1, 2]}),
        ("x=null", {"x": None}),
    ],
)
def test_parse_override(expression: str, expected: dict) -> None:
    assert parse_override(expression) == expected


# A malformed override is rejected with a clear message.
def test_parse_override_requires_equals_sign() -> None:
    with pytest.raises(ValueError, match="section.key=value"):
        parse_override("seed")


# A child config only lists what it changes; everything else is inherited.
def test_extends_and_overrides(tmp_path: Path) -> None:
    _write(
        tmp_path / "base.yaml",
        """
        name: base
        seed: 1
        paths:
          raw_log: base.log
          output_dir: out/base
        """,
    )
    child = _write(
        tmp_path / "child.yaml",
        """
        extends: base.yaml
        name: child
        paths:
          raw_log: child.log
        """,
    )
    config = load_config(child, overrides=["seed=5"])
    assert config.name == "child"
    assert config.seed == 5
    assert config.paths.raw_log == "child.log"
    assert config.paths.output_dir == "out/base"  # inherited from base.yaml


# Typos in keys must fail loudly instead of being ignored.
def test_unknown_key_raises(tmp_path: Path) -> None:
    bad = _write(tmp_path / "bad.yaml", "paths:\n  raw_lgo: typo.log\n")
    with pytest.raises(KeyError, match="raw_lgo"):
        load_config(bad)
