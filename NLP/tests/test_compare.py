"""Tests for the final comparison table."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from logad.evaluation.compare import (
    available_methods,
    comparison_markdown,
    comparison_table,
)
from logad.evaluation.predictions import save_method_result, save_window_predictions

TEST_WINDOWS = pd.DataFrame({"window_id": [10, 11, 12, 13], "label": [1, 0, 1, 0]})


# Metrics are recomputed from predictions; result files add timing.
def test_comparison_table(tmp_path: Path) -> None:
    save_window_predictions(tmp_path, "keyword_rules", np.array([10, 11, 12, 13]), [1, 0, 0, 0])
    save_window_predictions(tmp_path, "logbert", np.array([13, 12, 11, 10]), [1, 1, 1, 1])
    save_method_result(
        tmp_path, "keyword_rules", {"inference_seconds": 2.0, "test_message_level": {"f1": 0.5}}
    )

    assert available_methods(tmp_path) == ["logbert", "keyword_rules"]  # display order
    table = comparison_table(tmp_path, TEST_WINDOWS).set_index("method")
    assert table.loc["logbert", "recall"] == 1.0 and table.loc["logbert", "fp"] == 2
    assert table.loc["keyword_rules", "precision"] == 1.0
    assert table.loc["keyword_rules", "recall"] == 0.5
    assert table.loc["keyword_rules", "ms_per_window"] == 500.0
    assert pd.isna(table.loc["logbert", "inference_seconds"])

    markdown = comparison_markdown(table.reset_index(), n_windows=4, n_anomalous=2)
    assert "Test set: 4 windows, 2 anomalous (50.0%)." in markdown
    assert "| B4 Keyword rules (no LLM) | 1.000 | 0.500 | 0.667 | 0 | 1 | 0.500 | 2.0 |" in markdown


# A method evaluated on different windows is rejected, not silently compared.
def test_mismatched_windows_are_rejected(tmp_path: Path) -> None:
    save_window_predictions(tmp_path, "logbert", np.array([10, 11, 12]), [1, 0, 1])
    with pytest.raises(ValueError, match="1 missing"):
        comparison_table(tmp_path, TEST_WINDOWS)
