"""Shared pytest fixtures: synthetic BGL logs with a known structure."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest


# Format one line exactly like Loghub's BGL.log, deriving the date and time
# columns from the epoch so the synthetic lines look like the real ones.
def bgl_line(
    epoch: int,
    content: str,
    tag: str = "-",
    component: str = "KERNEL",
    level: str = "INFO",
    node: str = "R02-M1-N0-C:J12-U11",
) -> str:
    moment = datetime.fromtimestamp(epoch, tz=timezone.utc)
    date = moment.strftime("%Y.%m.%d")
    time = moment.strftime("%Y-%m-%d-%H.%M.%S.000000")
    return f"{tag} {epoch} {date} {node} {time} {node} RAS {component} {level} {content}"


# Expose the line builder to tests as a fixture.
@pytest.fixture
def make_bgl_line():
    return bgl_line


# A small synthetic BGL log spanning 100 minutes with one message every
# 30 seconds. Messages cycle through four normal templates; every message in
# minutes 70-72 is an alert ("data TLB error interrupt", tag KERNDTLB). The
# chronological 60/20/20 split therefore puts the alerts in the validation
# period, and the test period is entirely normal.
@pytest.fixture
def synthetic_bgl_file(tmp_path: Path) -> Path:
    start = 1_117_838_570  # first epoch of the real BGL log (2005-06-03)
    normal_messages = [
        "instruction cache parity error corrected",
        "{n} double-hummer alignment exceptions",
        "generating core.{n}",
        "CE sym {n}, at 0x{n:08x}, mask 0x{m:02x}",
    ]
    lines = []
    for i in range(200):  # 200 messages * 30 s = 100 minutes
        epoch = start + 30 * i
        minute = (epoch - start) // 60
        if 70 <= minute <= 72:
            lines.append(bgl_line(epoch, "data TLB error interrupt", tag="KERNDTLB", level="FATAL"))
        else:
            template = normal_messages[i % len(normal_messages)]
            lines.append(bgl_line(epoch, template.format(n=1000 + i, m=i % 256)))

    path = tmp_path / "BGL_synthetic.log"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
