"""Reading raw BGL (BlueGene/L) system logs from Loghub.

Each line of Loghub's ``BGL.log`` has ten space-separated fields::

    <AlertTag> <Epoch> <Date> <Node> <Time> <NodeRepeat> <Type> <Component> <Level> <Content>

for example::

    - 1117838570 2005.06.03 R02-M1-N0-C:J12-U11 2005-06-03-15.42.50.675872 R02-M1-N0-C:J12-U11 RAS KERNEL INFO instruction cache parity error corrected
    KERNDTLB 1118536327 2005.06.11 R30-M0-N9-C:J16-U01 2005-06-11-17.32.07.581048 R30-M0-N9-C:J16-U01 RAS KERNEL FATAL data TLB error interrupt

This is the layout LogBERT's preprocessing uses
(``<Label> <Id> <Date> <Code1> <Time> <Code2> <Component1> <Component2> <Level> <Content>``).

Preventing label leakage
------------------------
The first field is the *alert tag*: ``-`` for a normal message, otherwise an
alert category such as ``KERNDTLB`` or ``APPREAD``. It is the ground truth, so
it is split off here and kept only in the ``alert_tag`` / ``label`` columns.
Every model in this project reads the ``content`` column (or Drain templates
built from it) and therefore can never see the label.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from logad.utils import get_logger

logger = get_logger(__name__)

# Alert tag that marks a normal (non-alert) message.
NORMAL_TAG = "-"

# Number of whitespace-separated fields in front of the free-text content.
_N_HEADER_FIELDS = 9


# One parsed BGL log line. Only the fields the project uses are kept; the
# date string, the repeated node name and the RAS/NULL type are dropped.
@dataclass(frozen=True)
class LogRecord:
    alert_tag: str  # "-" for normal, otherwise the alert category (the label)
    timestamp: int  # Unix epoch seconds (UTC), used for ordering and windowing
    node: str  # hardware location, e.g. "R02-M1-N0-C:J12-U11"
    component: str  # subsystem that emitted the message, e.g. "KERNEL", "APP"
    level: str  # severity reported by the system, e.g. "INFO", "FATAL"
    content: str  # free-text message; the only text the models read

    # True when the alert tag marks this message as an anomaly.
    @property
    def is_anomalous(self) -> bool:
        return self.alert_tag != NORMAL_TAG


# Parse one raw BGL line into a LogRecord.
#
# The line is split on whitespace at most nine times, so the content keeps its
# internal spacing. A line with exactly nine fields is accepted with empty
# content. Returns None for malformed lines (too few fields or a non-numeric
# epoch) so the caller can count and skip them instead of crashing halfway
# through a 4.7-million-line file.
def parse_bgl_line(line: str) -> LogRecord | None:
    parts = line.rstrip("\r\n").split(None, _N_HEADER_FIELDS)
    if len(parts) < _N_HEADER_FIELDS:
        return None
    try:
        timestamp = int(parts[1])
    except ValueError:
        return None

    content = parts[9] if len(parts) > _N_HEADER_FIELDS else ""
    return LogRecord(
        alert_tag=parts[0],
        timestamp=timestamp,
        node=parts[3],
        component=parts[7],
        level=parts[8],
        content=content.strip(),
    )


# Read a BGL log file into a DataFrame with one row per valid line.
#
# Columns:
#   line_id    0-based line number in the raw file (for tracing errors back)
#   timestamp  int64 epoch seconds
#   node, component, level, content
#   alert_tag  "-" or the alert category (ground truth, never shown to models)
#   label      int8, 1 if the message is an alert, else 0
#
# `max_lines` limits how many lines are read, which is handy for quick tests.
# Undecodable bytes are replaced instead of aborting the whole read.
def read_bgl(path: str | Path, max_lines: int | None = None) -> pd.DataFrame:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"BGL log not found at {path}. Run `python scripts/download_data.py` first."
        )

    # Collect columns as plain lists; building the DataFrame once at the end is
    # far faster than appending row by row.
    columns: dict[str, list] = {
        "line_id": [],
        "timestamp": [],
        "node": [],
        "component": [],
        "level": [],
        "content": [],
        "alert_tag": [],
    }
    n_skipped = 0
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line_id, line in enumerate(fh):
            if max_lines is not None and line_id >= max_lines:
                break
            record = parse_bgl_line(line)
            if record is None:
                n_skipped += 1
                continue
            columns["line_id"].append(line_id)
            columns["timestamp"].append(record.timestamp)
            columns["node"].append(record.node)
            columns["component"].append(record.component)
            columns["level"].append(record.level)
            columns["content"].append(record.content)
            columns["alert_tag"].append(record.alert_tag)

    frame = pd.DataFrame(columns)
    frame["line_id"] = frame["line_id"].astype(np.int64)
    frame["timestamp"] = frame["timestamp"].astype(np.int64)
    frame["label"] = (frame["alert_tag"] != NORMAL_TAG).astype(np.int8)
    # Low-cardinality text columns are stored as categoricals to save memory
    # (the full BGL file has ~4.7M rows).
    for column in ("node", "component", "level", "alert_tag"):
        frame[column] = frame[column].astype("category")

    logger.info(
        "Read %d messages from %s (%d malformed lines skipped, %.2f%% anomalous)",
        len(frame),
        path,
        n_skipped,
        100.0 * frame["label"].mean() if len(frame) else 0.0,
    )
    return frame


# Stable-sort messages by timestamp and renumber the index 0..n-1.
#
# Windows are later represented as contiguous [start, end) index ranges, which
# is only valid if the messages are in chronological order. A stable sort keeps
# the original file order for messages that share the same second.
def sort_chronologically(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.sort_values("timestamp", kind="mergesort").reset_index(drop=True)
