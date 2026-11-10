"""Integration test: synthetic raw log -> processed dataset on disk -> reload."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from logad.config import load_config
from logad.data.drain import TemplateExtractor
from logad.data.preprocess import preprocess
from logad.data.processed import DRAIN_FILE, load_processed


# Run the full pipeline on the synthetic fixture with outputs in tmp_path.
def _run(synthetic_bgl_file: Path, tmp_path: Path):
    config = load_config(
        "configs/bgl.yaml",
        overrides=[
            f"paths.raw_log={synthetic_bgl_file}",
            f"paths.processed_dir={tmp_path / 'processed'}",
        ],
    )
    return config, preprocess(config)


# Every message gets a template, a split and a label; alerts land in "val".
def test_messages_table(synthetic_bgl_file: Path, tmp_path: Path) -> None:
    _, logs = _run(synthetic_bgl_file, tmp_path)
    messages = logs.messages
    assert len(messages) == 200
    assert messages["timestamp"].is_monotonic_increasing
    assert set(messages["split"]) == {"train", "val", "test"}
    assert messages.loc[messages["label"] == 1, "split"].eq("val").all()
    # The alert tag never appears in the text that models read.
    assert not messages["content"].str.contains("KERNDTLB").any()


# Template counts add up and the alert template is fully anomalous.
def test_templates_table(synthetic_bgl_file: Path, tmp_path: Path) -> None:
    _, logs = _run(synthetic_bgl_file, tmp_path)
    templates = logs.templates
    assert templates["n_messages"].sum() == 200
    assert (templates[["n_train", "n_val", "n_test"]].sum(axis=1) == templates["n_messages"]).all()
    alert = templates[templates["template"] == "data TLB error interrupt"].iloc[0]
    assert alert["n_anomalous"] == alert["n_messages"] == 6  # minutes 70-72, every 30 s
    assert alert["alert_tags"] == "KERNDTLB"
    assert not templates["is_mixed"].any()


# Windows stay inside their split and only validation windows are anomalous.
def test_windows_table(synthetic_bgl_file: Path, tmp_path: Path) -> None:
    _, logs = _run(synthetic_bgl_file, tmp_path)
    windows = logs.windows
    split_of_message = logs.messages["split"].astype(str).to_numpy()
    for row in windows.itertuples():
        assert set(split_of_message[row.start_idx : row.end_idx]) == {row.split}
    assert windows.loc[windows["label"] == 1, "split"].eq("val").all()
    assert windows["label"].sum() > 0
    stats = logs.stats["splits"]
    assert sum(s["n_windows"] for s in stats.values()) == len(windows)


# Saved files reload into identical tables, and the parser is saved too.
def test_roundtrip(synthetic_bgl_file: Path, tmp_path: Path) -> None:
    config, logs = _run(synthetic_bgl_file, tmp_path)
    loaded = load_processed(config.paths.processed_dir)
    pd.testing.assert_frame_equal(
        loaded.windows.reset_index(drop=True), logs.windows.reset_index(drop=True)
    )
    assert np.array_equal(loaded.template_ids, logs.template_ids)
    assert loaded.template_text == logs.template_text
    assert loaded.stats["n_messages"] == 200

    extractor = TemplateExtractor.load(Path(config.paths.processed_dir) / DRAIN_FILE)
    template_id, _ = extractor.match("data TLB error interrupt")
    assert template_id is not None and logs.template_text[template_id] == "data TLB error interrupt"


# A window's sequence is the slice of template IDs it covers.
def test_window_sequence(synthetic_bgl_file: Path, tmp_path: Path) -> None:
    _, logs = _run(synthetic_bgl_file, tmp_path)
    first = logs.windows.iloc[0]
    sequence = logs.window_sequence(first.start_idx, first.end_idx)
    assert len(sequence) == first.n_messages == 10  # 5 minutes, one message per 30 s
