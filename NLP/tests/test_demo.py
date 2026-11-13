"""Tests for the demo back end (no UI, no network).

Rules used here are written by hand for the synthetic log; they exercise the
demo's plumbing and are not experiment results.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import torch

from logad.demo import UNSEEN, DemoEngine, server_reachable
from logad.evaluation.predictions import save_method_result
from logad.logbert.trainer import LogBERTTrainer, build_model, prepare_training_data
from logad.rules.rule import Rule, save_rules

ALERT_LINE = (
    "KERNDTLB 1117843000 2005.06.03 R30-M0-N9-C:J16-U01 2005-06-03-16.56.40.000000 "
    "R30-M0-N9-C:J16-U01 RAS KERNEL FATAL data TLB error interrupt"
)


# Train a tiny LogBERT, save tuned thresholds and hand-written rules for the
# synthetic dataset, then load everything into a DemoEngine.
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
    save_rules(
        [Rule("TLB errors are kernel failures", ("tlb error",), 1)],
        output_dir / "rules" / "rules.json",
    )
    return DemoEngine(config, enable_live_llm=False)


# A full BGL line loses its alert tag; the content maps to its template.
def test_parse_input_strips_alert_tag(engine: DemoEngine) -> None:
    parsed = engine.parse_input(ALERT_LINE)
    assert parsed.removed_tag == "KERNDTLB"
    assert parsed.content == "data TLB error interrupt"
    assert parsed.template == "data TLB error interrupt" and parsed.template_id != UNSEEN

    unseen = engine.parse_input("completely new message 42")
    assert unseen.removed_tag is None and unseen.template_id == UNSEEN


# An untouched normal window is judged normal by every available method.
def test_normal_window(engine: DemoEngine) -> None:
    window_id = int(engine.candidate_windows().loc[0, "window_id"])
    analysis = engine.analyze_window(window_id)
    verdicts = {v.method.split(":")[0]: v for v in analysis.verdicts}
    assert verdicts["A"].available and not verdicts["A"].anomalous
    assert verdicts["B4"].available and not verdicts["B4"].anomalous
    assert not verdicts["B3"].available  # no LLM decisions were saved
    assert (analysis.messages["LogBERT"] != "not masked").all()  # full coverage


# Inserting the alert line flips both LogBERT and the keyword rules.
def test_inserted_alert_is_detected(engine: DemoEngine) -> None:
    analysis = engine.analyze_line(ALERT_LINE)
    verdicts = {v.method.split(":")[0]: v for v in analysis.verdicts}
    assert verdicts["A"].anomalous and "MISSED" in verdicts["A"].summary
    assert "Without your line: 0 missed (normal)" in verdicts["A"].summary
    assert list(analysis.messages.columns[:3]) == ["#", "ground truth", "LogBERT"]
    assert verdicts["B4"].anomalous and "Rule #1" in verdicts["B4"].summary
    row = analysis.messages[analysis.messages["#"].str.contains("your line")].iloc[0]
    assert row["ground truth"] == "KERNDTLB" and row["keyword"] == "abnormal"


# Example lines come from the test period; candidate windows are normal.
def test_examples_and_windows(engine: DemoEngine) -> None:
    assert all(isinstance(line, str) and line for line in engine.example_lines())
    windows = engine.candidate_windows()
    assert not windows.empty and (windows["label"] == 0).all()


# Unreachable local servers are detected quickly.
def test_server_reachable() -> None:
    assert server_reachable(None)
    assert not server_reachable("http://127.0.0.1:9/v1", timeout=0.2)
