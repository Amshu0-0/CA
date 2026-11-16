"""Tests for the demo back end (no UI, no network).

The tiny LogBERT trained here exercises the demo's plumbing on the synthetic
log; it is not an experiment result.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import torch

from logad.demo import UNSEEN, DemoEngine
from logad.evaluation.predictions import save_method_result
from logad.logbert.trainer import LogBERTTrainer, build_model, prepare_training_data

ALERT_LINE = (
    "KERNDTLB 1117843000 2005.06.03 R30-M0-N9-C:J16-U01 2005-06-03-16.56.40.000000 "
    "R30-M0-N9-C:J16-U01 RAS KERNEL FATAL data TLB error interrupt"
)


# Train a tiny LogBERT, save its tuned thresholds for the synthetic dataset,
# then load everything into a DemoEngine.
@pytest.fixture
def engine(processed_synthetic) -> DemoEngine:
    config, logs = processed_synthetic
    output_dir = Path(config.paths.output_dir)
    torch.manual_seed(0)
    data = prepare_training_data(logs, config.logbert, seed=0)
    model = build_model(len(data.vocab), config.logbert)
    LogBERTTrainer(model, config.logbert, torch.device("cpu")).fit(
        data, output_dir / "logbert" / "model.pt"
    )
    # g = |vocab| -> only unseen (<unk>) keys count as misses.
    settings = {"g": len(data.vocab), "r": 0, "mask_ratio": 0.5, "full_coverage": True}
    save_method_result(output_dir, "logbert_full", {"settings": settings})
    return DemoEngine(config)


# A full BGL line loses its alert tag; the content maps to its template.
def test_parse_input_strips_alert_tag(engine: DemoEngine) -> None:
    parsed = engine.parse_input(ALERT_LINE)
    assert parsed.removed_tag == "KERNDTLB"
    assert parsed.content == "data TLB error interrupt"
    assert parsed.template == "data TLB error interrupt" and parsed.template_id != UNSEEN

    unseen = engine.parse_input("completely new message 42")
    assert unseen.removed_tag is None and unseen.template_id == UNSEEN


# An untouched normal window is judged normal.
def test_normal_window(engine: DemoEngine) -> None:
    window_id = int(engine.candidate_windows().loc[0, "window_id"])
    analysis = engine.analyze_window(window_id)
    assert analysis.verdict.available and not analysis.verdict.anomalous
    assert (analysis.messages["LogBERT"] != "not masked").all()  # full coverage


# Inserting the alert line, whose template LogBERT never saw, flips the verdict.
def test_inserted_alert_is_detected(engine: DemoEngine) -> None:
    analysis = engine.analyze_line(ALERT_LINE)
    assert analysis.verdict.anomalous and "MISSED" in analysis.verdict.summary
    assert "Without your line: 0 missed (normal)" in analysis.verdict.summary
    assert list(analysis.messages.columns[:3]) == ["#", "ground truth", "LogBERT"]
    row = analysis.messages[analysis.messages["#"].str.contains("your line")].iloc[0]
    assert row["ground truth"] == "KERNDTLB" and row["LogBERT"].startswith("MISSED")


# Example lines come from the test period; candidate windows are normal.
def test_examples_and_windows(engine: DemoEngine) -> None:
    assert all(isinstance(line, str) and line for line in engine.example_lines())
    windows = engine.candidate_windows()
    assert not windows.empty and (windows["label"] == 0).all()
