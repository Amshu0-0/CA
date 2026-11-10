"""Tests for Drain template extraction."""

from __future__ import annotations

from pathlib import Path

from logad.config import ParsingConfig
from logad.data.drain import DEFAULT_MASKING, TemplateExtractor

MESSAGES = [
    "63543 double-hummer alignment exceptions",
    "instruction cache parity error corrected",
    "120 double-hummer alignment exceptions",
    "generating core.2275",
    "generating core.18",
    "instruction cache parity error corrected",
    "ciod: failed to read message prefix on control stream (CioStream socket to 172.16.96.116:33569",
    "CE sym 2, at 0x0b85eee0, mask 0x05",
]


# Helper: fit an extractor on MESSAGES and return it with the per-message IDs.
def _fit() -> tuple[TemplateExtractor, list[int]]:
    extractor = TemplateExtractor()
    ids = extractor.fit_transform(MESSAGES, show_progress=False)
    return extractor, ids.tolist()


# Messages that differ only in numbers share a template with <*> wildcards.
def test_numbers_become_wildcards() -> None:
    extractor, ids = _fit()
    assert ids[0] == ids[2]
    assert extractor.templates[ids[0]] == "<*> double-hummer alignment exceptions"
    assert ids[3] == ids[4]
    assert extractor.templates[ids[3]] == "generating core.<*>"


# Template IDs are 1-based and numbered in order of first appearance.
def test_ids_follow_first_appearance() -> None:
    extractor, ids = _fit()
    assert ids[:2] == [1, 2]
    assert sorted(extractor.templates) == list(range(1, len(extractor.templates) + 1))
    assert len(ids) == len(MESSAGES)


# Hex values and IP addresses are masked before parsing.
def test_hex_and_ip_masking() -> None:
    extractor, ids = _fit()
    assert extractor.templates[ids[7]] == "CE sym <*>, at <*>, mask <*>"
    assert "<*>:<*>" in extractor.templates[ids[6]]  # IP:port


# match() maps a new message to an existing template without changing it.
def test_match_known_and_unknown_messages() -> None:
    extractor, ids = _fit()
    n_templates = len(extractor.templates)

    template_id, text = extractor.match("99999 double-hummer alignment exceptions")
    assert template_id == ids[0]
    assert text == "<*> double-hummer alignment exceptions"

    template_id, text = extractor.match("totally new message 42")
    assert template_id is None
    assert text == "totally new message <*>"  # masked form for display
    assert len(extractor.templates) == n_templates  # nothing was added


# A saved extractor matches messages exactly like the original.
def test_save_and_load_roundtrip(tmp_path: Path) -> None:
    extractor, _ = _fit()
    path = tmp_path / "drain.pkl"
    extractor.save(path)
    restored = TemplateExtractor.load(path)
    assert restored.templates == extractor.templates
    assert restored.match("generating core.7") == extractor.match("generating core.7")


# The config default and the module default must stay in sync.
def test_config_masking_matches_module_default() -> None:
    assert ParsingConfig().masking == list(DEFAULT_MASKING)
