"""End-to-end preprocessing: raw BGL log -> processed dataset on disk.

Steps (all settings come from the experiment config):

1. read the raw log and split off the alert tag            (bgl.py)
2. sort messages chronologically                           (bgl.py)
3. extract Drain templates from the message content        (drain.py)
4. assign each message to train / val / test by time       (split.py)
5. build 5-minute sliding windows inside each split        (windows.py)
6. summarise templates and compute dataset statistics
7. save everything under ``paths.processed_dir``           (processed.py)
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from logad.config import ExperimentConfig
from logad.data.bgl import read_bgl, sort_chronologically
from logad.data.drain import TemplateExtractor
from logad.data.processed import DRAIN_FILE, ProcessedLogs, save_processed
from logad.data.split import SPLITS, SplitBoundaries, assign_splits, compute_split_boundaries
from logad.data.windows import build_window_table
from logad.utils import get_logger

logger = get_logger(__name__)


# Format an epoch timestamp as a readable UTC string for stats and reports.
def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


# Build the template table: one row per template with its text, how often it
# occurs overall and per split, how many of those occurrences are alerts, and
# which alert categories it carries.
#
# The per-split label counts drive later stages: rule induction uses the
# training counts as labels, and the analysis uses `is_mixed` to measure how
# often messages that share a template carry different labels.
def summarize_templates(messages: pd.DataFrame, templates: pd.DataFrame) -> pd.DataFrame:
    grouped = messages.groupby("template_id", observed=True)
    table = pd.DataFrame(
        {
            "n_messages": grouped.size(),
            "n_anomalous": grouped["label"].sum(),
        }
    )
    # Per-split totals and alert counts, e.g. n_train / n_train_anomalous.
    for split in SPLITS:
        in_split = messages[messages["split"] == split].groupby("template_id", observed=True)
        table[f"n_{split}"] = in_split.size()
        table[f"n_{split}_anomalous"] = in_split["label"].sum()

    # Templates absent from a split get NaN counts above; make them 0.
    count_columns = list(table.columns)
    table[count_columns] = table[count_columns].fillna(0).astype(np.int64)

    # Alert categories seen for each template, e.g. "KERNDTLB" or "APPREAD;APPSEV".
    # The tag column is categorical, so convert to plain strings before joining.
    alerts = messages.loc[messages["label"] == 1, ["template_id", "alert_tag"]]
    alert_tags = (
        alerts["alert_tag"]
        .astype(str)
        .groupby(alerts["template_id"])
        .agg(lambda tags: ";".join(sorted(set(tags))))
    )
    table["alert_tags"] = alert_tags.reindex(table.index).fillna("").astype(str)

    table = table.reset_index()
    table["anomaly_rate"] = table["n_anomalous"] / table["n_messages"]
    # A template is "mixed" if some of its messages are alerts and some are not.
    table["is_mixed"] = (table["n_anomalous"] > 0) & (table["n_anomalous"] < table["n_messages"])

    table = templates.merge(table, on="template_id", how="left")
    return table.sort_values("template_id").reset_index(drop=True)


# Compute the summary numbers reported in the dataset section of the report:
# overall size and anomaly rate, per-split message/window counts, templates
# unseen during training, and template label purity.
def dataset_statistics(
    messages: pd.DataFrame,
    templates: pd.DataFrame,
    windows: pd.DataFrame,
    boundaries: SplitBoundaries,
) -> dict[str, Any]:
    timestamps = messages["timestamp"].to_numpy()
    train_templates = set(messages.loc[messages["split"] == "train", "template_id"])

    per_split: dict[str, Any] = {}
    for split in SPLITS:
        part = messages[messages["split"] == split]
        part_windows = windows[windows["split"] == split]
        split_templates = set(part["template_id"])
        per_split[split] = {
            "n_messages": int(len(part)),
            "n_anomalous_messages": int(part["label"].sum()),
            "n_windows": int(len(part_windows)),
            "n_anomalous_windows": int(part_windows["label"].sum()),
            "n_templates": len(split_templates),
            # Templates LogBERT never saw during training (become <unk> keys).
            "n_templates_unseen_in_train": len(split_templates - train_templates),
            "start": _iso(part["timestamp"].min()) if len(part) else None,
            "end": _iso(part["timestamp"].max()) if len(part) else None,
        }

    mixed = templates[templates["is_mixed"]]
    return {
        "n_messages": int(len(messages)),
        "n_anomalous_messages": int(messages["label"].sum()),
        "anomaly_rate": float(messages["label"].mean()) if len(messages) else 0.0,
        "n_templates": int(len(templates)),
        "first_message": _iso(timestamps.min()),
        "last_message": _iso(timestamps.max()),
        "split_boundaries": {
            "train_end": _iso(boundaries.train_end),
            "val_end": _iso(boundaries.val_end),
        },
        "splits": per_split,
        "template_purity": {
            "n_mixed_templates": int(len(mixed)),
            "messages_in_mixed_templates": int(mixed["n_messages"].sum()),
            "share_of_messages_in_mixed_templates": (
                float(mixed["n_messages"].sum() / len(messages)) if len(messages) else 0.0
            ),
        },
    }


# Run the full preprocessing pipeline described by `config` and save the
# result to `config.paths.processed_dir`. `max_lines` reads only the first N
# raw lines (useful for quick experiments). Returns the processed dataset.
def preprocess(config: ExperimentConfig, max_lines: int | None = None) -> ProcessedLogs:
    # 1-2. Read, strip labels from the text, and order by time.
    messages = sort_chronologically(read_bgl(config.paths.raw_log, max_lines=max_lines))
    if messages.empty:
        raise ValueError(f"No valid log lines found in {config.paths.raw_log}")

    # 3. Drain templates. Only the content column is parsed - never the tag.
    parsing = config.parsing
    extractor = TemplateExtractor(
        depth=parsing.depth,
        similarity_threshold=parsing.similarity_threshold,
        max_children=parsing.max_children,
        masking=parsing.masking,
    )
    messages["template_id"] = extractor.fit_transform(messages["content"].tolist())

    # 4. Chronological split.
    timestamps = messages["timestamp"].to_numpy()
    boundaries = compute_split_boundaries(
        timestamps, config.split.train, config.split.val, config.split.test, by=config.split.by
    )
    splits = assign_splits(timestamps, boundaries)
    messages["split"] = pd.Categorical(splits, categories=list(SPLITS))

    # 5. Sliding windows, built separately inside each split.
    windows = build_window_table(
        timestamps,
        messages["label"].to_numpy(),
        splits,
        size_seconds=config.windows.size_seconds,
        step_seconds=config.windows.step_seconds,
    )

    # 6. Template table and statistics.
    templates = summarize_templates(messages, extractor.templates_frame())
    stats = dataset_statistics(messages, templates, windows, boundaries)

    # 7. Persist everything, including the fitted parser for the demo.
    logs = ProcessedLogs(messages=messages, templates=templates, windows=windows, stats=stats)
    out_dir = save_processed(logs, config.paths.processed_dir)
    extractor.save(Path(out_dir) / DRAIN_FILE)
    logger.info("Saved processed dataset to %s", out_dir)
    return logs
