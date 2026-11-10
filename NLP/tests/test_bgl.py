"""Tests for reading raw BGL lines (and keeping the label away from models)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from logad.data.bgl import parse_bgl_line, read_bgl, sort_chronologically

# Two real lines from Loghub's BGL_2k.log (one normal, one alert).
NORMAL_LINE = (
    "- 1117838570 2005.06.03 R02-M1-N0-C:J12-U11 2005-06-03-15.42.50.675872 "
    "R02-M1-N0-C:J12-U11 RAS KERNEL INFO instruction cache parity error corrected"
)
ALERT_LINE = (
    "APPREAD 1117869872 2005.06.04 R04-M1-N4-I:J18-U11 2005-06-04-00.24.32.432192 "
    "R04-M1-N4-I:J18-U11 RAS APP FATAL ciod: failed to read message prefix on control stream "
    "(CioStream socket to 172.16.96.116:33569"
)


# A normal line is split into its fields and labeled normal.
def test_parse_normal_line() -> None:
    record = parse_bgl_line(NORMAL_LINE)
    assert record is not None
    assert record.alert_tag == "-"
    assert not record.is_anomalous
    assert record.timestamp == 1117838570
    assert record.node == "R02-M1-N0-C:J12-U11"
    assert record.component == "KERNEL"
    assert record.level == "INFO"
    assert record.content == "instruction cache parity error corrected"


# The alert tag is stored as the label and never leaks into the content.
def test_alert_tag_is_removed_from_content() -> None:
    record = parse_bgl_line(ALERT_LINE)
    assert record is not None
    assert record.is_anomalous
    assert record.alert_tag == "APPREAD"
    assert "APPREAD" not in record.content
    assert record.content.startswith("ciod: failed to read message prefix")


# Spacing inside the content is preserved exactly.
def test_content_keeps_internal_spacing(make_bgl_line) -> None:
    record = parse_bgl_line(make_bgl_line(1117838570, "a  b   c"))
    assert record is not None
    assert record.content == "a  b   c"


# A line with all header fields but no message text is accepted.
def test_line_without_content(make_bgl_line) -> None:
    record = parse_bgl_line(make_bgl_line(1117838570, "").rstrip())
    assert record is not None
    assert record.content == ""


# Lines that are too short or have a non-numeric epoch are rejected.
def test_malformed_lines_return_none() -> None:
    assert parse_bgl_line("") is None
    assert parse_bgl_line("- 1117838570 only three fields") is None
    assert parse_bgl_line(NORMAL_LINE.replace("1117838570", "not-a-number", 1)) is None


# read_bgl builds a typed DataFrame, skips bad lines and derives the label.
def test_read_bgl(tmp_path: Path) -> None:
    path = tmp_path / "mini.log"
    path.write_text("\n".join([NORMAL_LINE, "garbage line", ALERT_LINE]) + "\n", encoding="utf-8")
    frame = read_bgl(path)
    assert list(frame["line_id"]) == [0, 2]  # line 1 was malformed and skipped
    assert list(frame["label"]) == [0, 1]
    assert frame["timestamp"].dtype == np.int64
    assert list(frame["alert_tag"].astype(str)) == ["-", "APPREAD"]


# max_lines limits how much of the file is read.
def test_read_bgl_max_lines(synthetic_bgl_file: Path) -> None:
    assert len(read_bgl(synthetic_bgl_file, max_lines=10)) == 10


# Sorting is by timestamp and stable for messages within the same second.
def test_sort_chronologically_is_stable() -> None:
    frame = pd.DataFrame({"timestamp": [5, 3, 5, 1], "line_id": [0, 1, 2, 3]})
    ordered = sort_chronologically(frame)
    assert list(ordered["timestamp"]) == [1, 3, 5, 5]
    assert list(ordered["line_id"]) == [3, 1, 0, 2]  # ties keep file order
    assert list(ordered.index) == [0, 1, 2, 3]
