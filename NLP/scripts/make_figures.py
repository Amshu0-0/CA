"""Draw the figures of the project guide and the slides from a finished run.

Usage:
    python scripts/make_figures.py --config configs/bgl_cpu.yaml
    python scripts/make_figures.py --config configs/bgl_cpu.yaml --skip-model

Every number in a figure is computed from the artifacts of a real run, never
typed in:

* the preprocessed dataset (paths.processed_dir): the timestamp, label and
  template ID of every message, and the windows
* the run directory (paths.output_dir): metrics, window predictions,
  per-template decisions and analysis tables, as the pipeline wrote them or as
  `make restore` copied them back from results/
* the trained LogBERT checkpoint, for the figure that looks inside the model
  (left out with --skip-model, which needs no forward pass)

The PNGs are written to docs/figures/ (change with --out), together with
figure_data.json: the values behind every figure, so each chart has a table
twin. All figures share one look: the Inter typeface, a light surface,
hairline grids, thin marks with rounded data ends, and the same colour for the
same thing everywhere (blue = normal, orange = alert or anomalous).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")  # render to files only; no display needed

import matplotlib.dates as mdates  # noqa: E402 - must follow matplotlib.use
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap  # noqa: E402
from matplotlib.patches import PathPatch, Rectangle  # noqa: E402
from matplotlib.path import Path as MplPath  # noqa: E402
from matplotlib.ticker import FuncFormatter, PercentFormatter  # noqa: E402

from logad.config import ExperimentConfig, add_config_arguments, config_from_args  # noqa: E402
from logad.data.processed import ProcessedLogs, load_processed  # noqa: E402
from logad.data.windows import count_in_windows  # noqa: E402
from logad.evaluation.predictions import load_window_predictions  # noqa: E402
from logad.logbert.dataset import window_to_chunks  # noqa: E402
from logad.logbert.detector import LogBERTDetector  # noqa: E402
from logad.logbert.trainer import load_checkpoint  # noqa: E402
from logad.logbert.vocab import UNK_INDEX  # noqa: E402

# ----------------------------------------------------------------- the look

# Colours of the reference palette of the data-viz guidelines (light mode).
SURFACE = "#fcfcfb"  # chart background
INK = "#0b0b0b"  # primary text
INK_2 = "#52514e"  # secondary text: labels, values
MUTED = "#898781"  # axis text
GRID = "#e1e0d9"  # hairline gridlines
AXIS = "#c3c2b7"  # baselines, reference lines
BLUE = "#2a78d6"  # categorical slot 1: normal / Approach A
ORANGE = "#eb6834"  # categorical slot 2: alert, anomalous / Approach B
# Sequential one-hue ramp (steps 100 -> 700) for magnitude, e.g. heatmaps.
BLUE_RAMP = [
    "#cde2fb",
    "#b7d3f6",
    "#9ec5f4",
    "#86b6ef",
    "#6da7ec",
    "#5598e7",
    "#3987e5",
    "#2a78d6",
    "#256abf",
    "#1c5cab",
    "#184f95",
    "#104281",
    "#0d366b",
]

DPI = 200  # figures are drawn and saved at this resolution
GAP_PT = 2 * 72 / DPI  # a 2-pixel surface gap between touching marks, in points
RADIUS_PX = 6.0  # corner radius of a bar's data end, in pixels


# Global matplotlib settings shared by every figure.
def setup_style() -> None:
    plt.rcParams.update(
        {
            "font.family": "Inter",
            "font.size": 10,
            "figure.dpi": DPI,
            "figure.facecolor": SURFACE,
            "savefig.facecolor": SURFACE,
            "axes.facecolor": SURFACE,
            "axes.edgecolor": AXIS,
            "axes.linewidth": 0.8,
            "axes.labelcolor": INK_2,
            "axes.labelsize": 10,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.axisbelow": True,  # gridlines behind the data
            "xtick.major.size": 0,  # no tick marks: the grid carries position
            "ytick.major.size": 0,
            "xtick.major.pad": 6,
            "ytick.major.pad": 6,
            "xtick.labelcolor": INK_2,
            "ytick.labelcolor": INK_2,
            "xtick.labelsize": 9,
            "ytick.labelsize": 9,
            "grid.color": GRID,
            "grid.linewidth": 0.6,
            "grid.linestyle": "-",  # solid hairlines, never dashed
            "legend.frameon": False,
            "legend.fontsize": 9,
            "legend.labelcolor": INK_2,
        }
    )


# A figure with one axes whose margins are fixed in inches. Fixed margins keep
# the axes size stable, which the pixel-based corner radius of the bars needs.
def new_axes(
    width: float,
    height: float,
    left: float = 0.9,
    right: float = 0.3,
    bottom: float = 0.6,
    top: float = 0.45,
) -> tuple[plt.Figure, plt.Axes]:
    fig = plt.figure(figsize=(width, height))
    ax = fig.add_axes(
        (left / width, bottom / height, 1 - (left + right) / width, 1 - (bottom + top) / height)
    )
    return fig, ax


# Data units per screen pixel along x and y for the axes' current limits.
def data_per_pixel(ax: plt.Axes) -> tuple[float, float]:
    box = ax.get_window_extent()
    (x0, x1), (y0, y1) = ax.get_xlim(), ax.get_ylim()
    return (x1 - x0) / box.width, (y1 - y0) / box.height


# Draw one bar from `base` to `value` (along the value axis) centred on
# `center` (along the category axis) with the given `thickness`. The data end
# is rounded, the baseline end square. The radius is set in pixels and
# converted per axis, so the corners stay circular on any scale. Set the axes
# limits before calling: the conversion uses them.
def rounded_bar(
    ax: plt.Axes,
    base: float,
    value: float,
    center: float,
    thickness: float,
    color: str,
    horizontal: bool = False,
    round_end: bool = True,
    gap: bool = True,
) -> None:
    if value == base:
        return  # a zero-length bar draws nothing
    px_x, px_y = data_per_pixel(ax)
    # Corner radius along the value axis and across the bar, in data units.
    if horizontal:
        r_value, r_cross = RADIUS_PX * px_x, RADIUS_PX * px_y
    else:
        r_value, r_cross = RADIUS_PX * px_y, RADIUS_PX * px_x
    sign = 1.0 if value > base else -1.0
    # Shrink the corner for bars shorter or thinner than the radius.
    r_value = min(r_value, abs(value - base)) if round_end else 0.0
    r_cross = min(r_cross, thickness / 2) if round_end else 0.0
    lo, hi = center - thickness / 2, center + thickness / 2
    # Outline in (value axis, category axis) coordinates: along one side to
    # the data end, round the two corners, back along the other side.
    points = [
        (base, lo),
        (value - sign * r_value, lo),
        (value, lo),  # control point of the first corner
        (value, lo + r_cross),
        (value, hi - r_cross),
        (value, hi),  # control point of the second corner
        (value - sign * r_value, hi),
        (base, hi),
        (base, lo),
    ]
    codes = [
        MplPath.MOVETO,
        MplPath.LINETO,
        MplPath.CURVE3,
        MplPath.CURVE3,
        MplPath.LINETO,
        MplPath.CURVE3,
        MplPath.CURVE3,
        MplPath.LINETO,
        MplPath.CLOSEPOLY,
    ]
    if not horizontal:
        points = [(c, v) for v, c in points]  # swap to (x, y) = (category, value)
    patch = PathPatch(
        MplPath(points, codes),
        facecolor=color,
        # A surface-coloured edge leaves a 2-pixel gap between touching marks.
        edgecolor=SURFACE if gap else "none",
        linewidth=GAP_PT if gap else 0,
        zorder=2,
    )
    ax.add_patch(patch)


# Compact number labels: 950, 12K, 1.2M.
def compact(value: float, _position: Any = None) -> str:
    if abs(value) >= 1_000_000:
        return f"{value / 1_000_000:.1f}M".replace(".0M", "M")
    if abs(value) >= 1_000:
        return f"{value / 1_000:.0f}K"
    return f"{value:.0f}"


# Human-readable duration for a number of seconds: 8 ms, 78 s, 47 min.
def duration(seconds: float) -> str:
    if seconds < 1:
        return f"{seconds * 1000:.0f} ms"
    if seconds < 600:
        return f"{seconds:.0f} s"
    if seconds < 7200:
        return f"{seconds / 60:.0f} min"
    return f"{seconds / 3600:.1f} h"


# Save a figure as PNG in `out` and close it.
def save(fig: plt.Figure, out: Path, name: str) -> None:
    path = out / f"{name}.png"
    fig.savefig(path, dpi=DPI, bbox_inches="tight", pad_inches=0.1)
    plt.close(fig)
    print(f"  wrote {path}")


# ------------------------------------------------------------ data helpers


# Start of each message's week (Monday), as dates matplotlib can plot.
def message_weeks(logs: ProcessedLogs) -> pd.Series:
    stamps = pd.to_datetime(logs.messages["timestamp"].to_numpy(), unit="s")
    return pd.Series(stamps.to_period("W-SUN").start_time, index=logs.messages.index)


# First timestamp of the validation and test periods: where the split cuts.
def split_starts(logs: ProcessedLogs) -> dict[str, pd.Timestamp]:
    split = logs.messages["split"].astype(str)
    stamps = logs.messages["timestamp"]
    return {name: pd.to_datetime(stamps[split == name].min(), unit="s") for name in ("val", "test")}


# Draw the two split boundaries and name the three periods. `y` is a fraction
# of the axes height (above 1 puts the names above the plot); `notes` adds a
# second line per period ("train", "val", "test" keys).
def mark_splits(
    ax: plt.Axes, logs: ProcessedLogs, y: float, notes: dict[str, str] | None = None
) -> None:
    starts = split_starts(logs)
    stamps = logs.messages["timestamp"]
    first = pd.to_datetime(stamps.min(), unit="s")
    last = pd.to_datetime(stamps.max(), unit="s")
    edges = [first, starts["val"], starts["test"], last]
    names = {
        "train": "Training (first 60% of the time)",
        "val": "Validation (20%)",
        "test": "Test (20%)",
    }
    for boundary in edges[1:3]:
        ax.axvline(boundary, color=AXIS, linewidth=1.0, zorder=1)
    # x in data coordinates (dates), y as a fraction of the axes height.
    blended = ax.get_xaxis_transform()
    for (start, end), key in zip(zip(edges[:-1], edges[1:], strict=True), names, strict=True):
        middle = start + (end - start) / 2
        ax.text(
            middle,
            y,
            names[key],
            transform=blended,
            ha="center",
            va="bottom",
            color=INK_2,
            fontsize=9,
        )
        if notes and key in notes:
            ax.text(
                middle,
                y - 0.02,
                notes[key],
                transform=blended,
                ha="center",
                va="top",
                color=INK,
                fontsize=9,
                fontweight="semibold",
            )


# Templates that never occur in the training period (n_train == 0).
def unseen_in_training(logs: ProcessedLogs) -> np.ndarray:
    templates = logs.templates
    unseen_ids = templates.loc[templates["n_train"] == 0, "template_id"].to_numpy()
    return np.isin(logs.template_ids, unseen_ids)


# --------------------------------------------------------------- figures


# 1. Messages per week, normal and alert, with the chronological split.
def fig_timeline(logs: ProcessedLogs, out: Path, data: dict) -> None:
    weeks = message_weeks(logs)
    labels = logs.messages["label"].to_numpy()
    table = (
        pd.DataFrame({"week": weeks, "alert": labels})
        .groupby("week")["alert"]
        .agg(total="size", alerts="sum")
    )
    table["normal"] = table["total"] - table["alerts"]

    fig, ax = new_axes(10, 3.9, top=0.75)
    x = mdates.date2num(table.index.to_pydatetime()) + 3.5  # centre of each week
    top = table["total"].max() * 1.12
    ax.set_xlim(x.min() - 5, x.max() + 5)
    ax.set_ylim(0, top)
    for xi, normal, alerts in zip(x, table["normal"], table["alerts"], strict=True):
        rounded_bar(ax, 0, normal, xi, 4.6, BLUE, round_end=alerts == 0)
        rounded_bar(ax, normal, normal + alerts, xi, 4.6, ORANGE)

    mark_splits(ax, logs, 0.975)
    ax.xaxis_date()
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
    ax.xaxis.set_major_locator(mdates.MonthLocator())
    ax.yaxis.set_major_formatter(FuncFormatter(compact))
    ax.grid(axis="y")
    ax.set_ylabel("Messages per week")
    ax.legend(
        handles=[
            Rectangle((0, 0), 1, 1, color=BLUE, label="Normal messages"),
            Rectangle((0, 0), 1, 1, color=ORANGE, label="Alert messages"),
        ],
        loc="lower left",
        bbox_to_anchor=(0, 1.08),
        ncol=2,
        borderaxespad=0,
    )
    save(fig, out, "01_bgl_timeline")
    data["timeline"] = {
        "weeks": [d.strftime("%Y-%m-%d") for d in table.index],
        "normal": table["normal"].astype(int).tolist(),
        "alerts": table["alerts"].astype(int).tolist(),
    }


# 2. Share of each week's messages whose template never appears in training.
def fig_unseen_share(logs: ProcessedLogs, out: Path, data: dict) -> None:
    unseen = unseen_in_training(logs)
    weeks = message_weeks(logs)
    share = pd.Series(unseen).groupby(weeks.to_numpy()).mean() * 100
    split = logs.messages["split"].astype(str).to_numpy()
    by_split = {name: float(unseen[split == name].mean() * 100) for name in ("val", "test")}

    fig, ax = new_axes(10, 3.6, top=0.85)
    x = mdates.date2num(share.index.to_pydatetime()) + 3.5
    ax.set_xlim(x.min() - 5, x.max() + 5)
    ax.set_ylim(0, 100)
    ax.plot(x, share.to_numpy(), color=BLUE, linewidth=2, solid_capstyle="round", zorder=3)
    ax.plot(
        x[-1],
        share.iloc[-1],
        "o",
        color=BLUE,
        markersize=8,
        zorder=4,
        markeredgecolor=SURFACE,
        markeredgewidth=GAP_PT,
    )
    # Period names above the plot, each with its share of new-template messages.
    mark_splits(
        ax,
        logs,
        1.16,
        notes={
            "train": "0% (by definition)",
            "val": f"{by_split['val']:.1f}% of messages",
            "test": f"{by_split['test']:.1f}% of messages",
        },
    )
    ax.xaxis_date()
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
    ax.xaxis.set_major_locator(mdates.MonthLocator())
    ax.yaxis.set_major_formatter(PercentFormatter(decimals=0))
    ax.set_yticks([0, 25, 50, 75, 100])
    ax.grid(axis="y")
    ax.set_ylabel("Messages with a template\nnever seen in training")
    save(fig, out, "02_unseen_templates")
    data["unseen_templates"] = {
        "weekly_percent": {d.strftime("%Y-%m-%d"): round(float(v), 2) for d, v in share.items()},
        "percent_by_split": {k: round(v, 2) for k, v in by_split.items()},
    }


# 3. LogBERT's training and dev loss per epoch.
def fig_training_curve(output_dir: Path, out: Path, data: dict) -> None:
    history = json.loads((output_dir / "logbert" / "history.json").read_text())
    epochs = [h["epoch"] for h in history]
    train = [h["train_loss"] for h in history]
    dev = [h["dev_loss"] for h in history]
    best = int(np.argmin(dev))

    fig, ax = new_axes(7, 3.6, right=1.6, top=0.3)
    for values, color, name in ((train, BLUE, "Training loss"), (dev, ORANGE, "Dev loss")):
        ax.plot(
            epochs,
            values,
            color=color,
            linewidth=2,
            marker="o",
            markersize=8,
            markeredgecolor=SURFACE,
            markeredgewidth=GAP_PT,
            label=name,
            zorder=3,
        )
        ax.text(epochs[-1] + 0.15, values[-1], name, va="center", color=INK_2, fontsize=9)
    ax.annotate(
        f"best dev loss {dev[best]:.2f}\n(epoch {epochs[best]}, kept)",
        xy=(epochs[best], dev[best]),
        xytext=(epochs[best] - 1.1, dev[best] + 0.45),
        color=INK,
        fontsize=9,
        arrowprops={"arrowstyle": "-", "color": AXIS, "linewidth": 1},
    )
    ax.set_xlim(0.7, epochs[-1] + 0.3)
    ax.set_ylim(0, max(train) * 1.1)
    ax.set_xticks(epochs)
    ax.set_xlabel("Epoch")
    ax.set_ylabel("Loss (MLKP + 0.1 × VHM)")
    ax.grid(axis="y")
    ax.legend(loc="upper right", bbox_to_anchor=(1.0, 1.0))
    save(fig, out, "03_logbert_training")
    data["training"] = {"epoch": epochs, "train_loss": train, "dev_loss": dev}


# 4. Validation F1 for every (g, r) pair LogBERT was tuned over.
def fig_tuning_grid(output_dir: Path, out: Path, data: dict) -> None:
    grid = pd.read_csv(output_dir / "logbert" / "logbert_tuning_grid.csv")
    chosen = json.loads((output_dir / "results" / "logbert.json").read_text())["settings"]
    table = grid.pivot(index="r", columns="g", values="f1").sort_index()
    gs, rs = table.columns.to_numpy(), table.index.to_numpy()
    best = float(table.to_numpy().max())

    fig, ax = new_axes(8, 4.9, right=1.4, top=0.65)
    cmap = LinearSegmentedColormap.from_list("blue_sequential", BLUE_RAMP)
    image = ax.imshow(
        table.to_numpy(),
        origin="lower",
        aspect="auto",
        cmap=cmap,
        vmin=float(table.to_numpy().min()),
        vmax=best,
        interpolation="nearest",
    )
    # Outline the settings within 0.02 of the best F1: the "good region".
    good = 0
    for i, r in enumerate(rs):
        for j, g in enumerate(gs):
            if table.loc[r, g] >= best - 0.02:
                good += 1
                ax.add_patch(
                    Rectangle(
                        (j - 0.5, i - 0.5),
                        1,
                        1,
                        fill=False,
                        edgecolor=SURFACE,
                        linewidth=1.2,
                        zorder=3,
                    )
                )
    j, i = list(gs).index(chosen["g"]), list(rs).index(chosen["r"])
    ax.plot(
        j, i, "o", markersize=8, color=SURFACE, markeredgecolor=INK, markeredgewidth=1.0, zorder=4
    )
    ax.text(
        0,
        1.03,
        f"Dot: the pair chosen on validation, g = {chosen['g']} and r = {chosen['r']} "
        f"(F1 {table.loc[chosen['r'], chosen['g']]:.3f}).  Outlined: the {good} of {table.size} "
        "pairs within 0.02 of the best F1.",
        transform=ax.transAxes,
        color=INK_2,
        fontsize=9,
        va="bottom",
    )
    ax.set_xticks(range(len(gs)), [str(g) for g in gs])
    ax.set_yticks(range(0, len(rs), 2), [str(r) for r in rs[::2]])
    ax.set_xlabel("g: a key counts as predicted if it is among the model's top g guesses")
    ax.set_ylabel("r: flag a sequence with more than r missed keys")
    for spine in ax.spines.values():
        spine.set_visible(False)
    cax = fig.add_axes(
        (ax.get_position().x1 + 0.02, ax.get_position().y0, 0.018, ax.get_position().height)
    )
    bar = fig.colorbar(image, cax=cax)
    bar.outline.set_visible(False)
    bar.set_label("Validation F1", color=INK_2)
    bar.ax.tick_params(labelcolor=INK_2, size=0)
    save(fig, out, "04_logbert_tuning_grid")
    data["tuning_grid"] = {
        "best_validation_f1": best,
        "chosen": chosen,
        "settings_within_0.02_of_best": int((table.to_numpy() >= best - 0.02).sum()),
        "n_settings": int(table.size),
    }


# Non-overlapping windows of a period: windows slide by 1 minute but span 5,
# so keep each window that starts after the previous kept one ended. Every
# message is then counted about once.
def disjoint_windows(windows: pd.DataFrame) -> pd.DataFrame:
    keep, last_end = [], -1
    for row in windows.sort_values("start_idx").itertuples():
        if row.start_idx >= last_end:
            keep.append(row.Index)
            last_end = row.end_idx
    return windows.loc[keep]


# Run LogBERT over the test period with every key masked once and return,
# per message, the rank of its true key among the model's predictions.
def key_ranks(config: ExperimentConfig, logs: ProcessedLogs) -> pd.DataFrame:
    checkpoint = Path(config.paths.output_dir) / "logbert" / "model.pt"
    model, vocab, _, _ = load_checkpoint(checkpoint)
    detection = config.logbert.detection
    detector = LogBERTDetector(
        model,
        vocab,
        mask_ratio=detection.mask_ratio,
        full_coverage=True,  # check every key, not a random half
        batch_size=detection.batch_size,
        seed=detection.seed,
    )
    body = model.max_len - 1  # keys per chunk after the <dist> token
    sequences, owners = [], []
    for row in disjoint_windows(logs.windows_in("test")).itertuples():
        encoded = vocab.encode(logs.template_ids[row.start_idx : row.end_idx])
        for c, chunk in enumerate(window_to_chunks(encoded, model.max_len)):
            sequences.append(chunk)
            owners.append(row.start_idx + c * body)  # message index of the chunk's first key
    print(f"  scoring {len(sequences):,} chunks with LogBERT (every key masked once)...")
    predictions = detector.predict_sequences(sequences)

    index = np.concatenate(
        [owner + p.positions - 1 for owner, p in zip(owners, predictions, strict=True)]
    )
    ranks = np.concatenate([p.ranks for p in predictions])
    return pd.DataFrame(
        {
            "message": index,
            "rank": ranks,
            "alert": logs.message_labels[index] == 1,
            "known": vocab.encode(logs.template_ids[index]) != UNK_INDEX,
        }
    )


# 5. Inside LogBERT: how often it fails to predict a masked key, by message
# type. Unseen templates become <unk> and are always a miss.
def fig_key_misses(ranks: pd.DataFrame, g: int, out: Path, data: dict) -> None:
    ranks = ranks.assign(missed=ranks["rank"] >= g)
    groups = [
        ("Template seen in normal\ntraining windows", True),
        ("Template new to LogBERT\n(read as <unk>)", False),
    ]
    stats: dict[str, dict[str, Any]] = {}
    fig, ax = new_axes(8.6, 3.4, left=2.0, right=1.9, top=0.55)
    ax.set_xlim(0, 100)
    ax.set_ylim(-0.6, len(groups) - 0.4)
    for row, (_name, known) in enumerate(groups):
        y = len(groups) - 1 - row
        for offset, alert, color in ((0.17, False, BLUE), (-0.17, True, ORANGE)):
            part = ranks[(ranks["known"] == known) & (ranks["alert"] == alert)]
            key = f"{'known' if known else 'new'}_{'alert' if alert else 'normal'}"
            if part.empty:
                ax.text(
                    1, y + offset, "none in the test period", va="center", color=MUTED, fontsize=9
                )
                stats[key] = {"keys": 0}
                continue
            share = float(part["missed"].mean() * 100)
            rounded_bar(ax, 0, share, y + offset, 0.3, color, horizontal=True)
            ax.text(
                share + 1.2,
                y + offset,
                f"{share:.0f}% of {len(part):,} keys",
                va="center",
                color=INK_2,
                fontsize=9,
            )
            stats[key] = {"keys": int(len(part)), "percent_missed": round(share, 2)}
    ax.set_yticks([len(groups) - 1 - i for i in range(len(groups))], [n for n, _ in groups])
    ax.xaxis.set_major_formatter(PercentFormatter(decimals=0))
    ax.set_xlabel(f"Keys the model fails to predict (true key not in its top {g} guesses)")
    ax.grid(axis="x")
    ax.spines["left"].set_visible(False)
    ax.legend(
        handles=[
            Rectangle((0, 0), 1, 1, color=BLUE, label="Normal messages"),
            Rectangle((0, 0), 1, 1, color=ORANGE, label="Alert messages"),
        ],
        loc="lower left",
        bbox_to_anchor=(0, 1.04),
        ncol=2,
        borderaxespad=0,
    )
    save(fig, out, "05_logbert_key_misses")
    data["key_misses"] = {"g": g, "groups": stats}


# 6. Inside LogBERT: the window score (most missed keys in any chunk) for
# three kinds of test window, with the decision threshold r.
def fig_window_scores(
    output_dir: Path, logs: ProcessedLogs, out: Path, data: dict, cap: int = 40
) -> None:
    settings = json.loads((output_dir / "results" / "logbert.json").read_text())["settings"]
    r = settings["r"]
    windows = logs.windows_in("test").reset_index(drop=True)
    scores = (
        load_window_predictions(output_dir, "logbert")
        .set_index("window_id")
        .loc[windows["window_id"], "score"]
        .to_numpy()
    )
    pairs = windows[["start_idx", "end_idx"]].to_numpy()
    has_unseen = count_in_windows(unseen_in_training(logs).astype(np.int64), pairs) > 0
    anomalous = windows["label"].to_numpy() == 1
    panels = [
        ("Normal windows, every template seen in training", ~anomalous & ~has_unseen, BLUE),
        ("Normal windows with a template never seen in training", ~anomalous & has_unseen, BLUE),
        ("Anomalous windows (contain an alert)", anomalous, ORANGE),
    ]

    fig, axes = plt.subplots(3, 1, figsize=(9, 6.6), sharex=True)
    fig.subplots_adjust(left=0.09, right=0.98, bottom=0.09, top=0.95, hspace=0.55)
    stats = {}
    for ax, (title, mask, color) in zip(axes, panels, strict=True):
        clipped = np.minimum(scores[mask], cap)
        counts = np.bincount(clipped.astype(int), minlength=cap + 1)
        shares = counts / max(1, mask.sum()) * 100
        flagged = float((scores[mask] > r).mean() * 100)
        ax.set_xlim(-0.8, cap + 0.8)
        ax.set_ylim(0, shares.max() * 1.18)
        for k, share in enumerate(shares):
            rounded_bar(ax, 0, share, k, 0.72, color)
        ax.axvline(r + 0.5, color=INK_2, linewidth=1.0, zorder=3)
        ax.text(0, 1.06, title, transform=ax.transAxes, color=INK, fontsize=10, va="bottom")
        ax.text(
            1,
            1.06,
            f"{mask.sum():,} windows · flagged: {flagged:.1f}%",
            transform=ax.transAxes,
            ha="right",
            color=INK_2,
            fontsize=9,
            va="bottom",
        )
        ax.yaxis.set_major_formatter(PercentFormatter(decimals=0))
        ax.grid(axis="y")
        stats[title] = {"windows": int(mask.sum()), "percent_flagged": round(flagged, 2)}
    axes[0].text(
        r + 0.9,
        axes[0].get_ylim()[1] * 0.75,
        f"flagged if more than r = {r}\nmissed keys",
        color=INK_2,
        fontsize=9,
        va="top",
    )
    axes[-1].set_xticks(
        list(range(0, cap + 1, 5)), [str(k) if k < cap else f"{cap}+" for k in range(0, cap + 1, 5)]
    )
    axes[-1].set_xlabel(
        f"Window score: most missed keys in any chunk (top-{settings['g']} predictions)"
    )
    fig.text(0.015, 0.52, "Share of windows", rotation=90, va="center", color=INK_2)
    save(fig, out, "06_logbert_window_scores")
    data["window_scores"] = {"g": settings["g"], "r": r, "panels": stats}


# Short method names for the results figures.
SHORT_NAMES = {
    "logbert": "A  LogBERT",
    "logbert_full": "A+ LogBERT, full coverage",
    "llm_zero_shot": "B1 LLM zero-shot",
    "llm_few_shot": "B2 LLM few-shot",
    "llm_rule_based": "B3 LLM rule-based",
    "keyword_rules": "B4 keyword rules",
}


# 7. Every method in precision-recall space, with curves of equal F1.
def fig_pr_space(output_dir: Path, out: Path, data: dict) -> None:
    table = pd.read_csv(output_dir / "results" / "comparison.csv").set_index("method")
    fig, ax = new_axes(8, 5.0, left=0.9, right=0.35, top=0.55)
    y_min = 0.84  # every method's recall is above 0.89, so zoom in on the top
    ax.set_xlim(0, 1.04)
    ax.set_ylim(y_min, 1.02)
    # Iso-F1 curves: all (precision, recall) pairs with the same F1. Solving
    # F1 = 2PR / (P + R) for P gives P = F1 * R / (2R - F1); sampling along
    # recall keeps even the steep low-F1 curves smooth. Each curve is labelled
    # where it leaves the plot at the bottom.
    recall = np.linspace(y_min, 1.02, 200)
    for level in (0.2, 0.4, 0.6, 0.8, 0.9):
        precision = level * recall / (2 * recall - level)
        ax.plot(precision, recall, color=AXIS, linewidth=1.0, zorder=1)
        ax.text(
            precision[0] + 0.008,
            y_min + 0.004,
            f"F1 = {level}",
            color=MUTED,
            fontsize=8,
            va="bottom",
        )
    # Label offsets (points) chosen so that no two labels collide.
    offsets = {
        "logbert": (10, 0, "left"),
        "logbert_full": (10, -12, "left"),
        "llm_zero_shot": (10, 0, "left"),
        "llm_few_shot": (0, -15, "center"),
        "llm_rule_based": (0, 14, "center"),
        "keyword_rules": (-10, 13, "right"),
    }
    for method, row in table.iterrows():
        color = BLUE if method.startswith("logbert") else ORANGE  # Approach A or B
        ax.plot(
            row["precision"],
            row["recall"],
            "o",
            markersize=10,
            color=color,
            markeredgecolor=SURFACE,
            markeredgewidth=GAP_PT * 1.5,
            zorder=4,
        )
        dx, dy, align = offsets[method]
        ax.annotate(
            f"{SHORT_NAMES[method]}  (F1 {row['f1']:.3f})",
            (row["precision"], row["recall"]),
            xytext=(dx, dy),
            textcoords="offset points",
            ha=align,
            va="center",
            color=INK,
            fontsize=9,
            zorder=5,
            # A surface-coloured box hides the curves behind the label.
            bbox={"facecolor": SURFACE, "edgecolor": "none", "pad": 1.5},
        )
    ax.set_yticks([0.85, 0.90, 0.95, 1.00])
    ax.set_xlabel("Precision: share of flagged windows that are really anomalous")
    ax.set_ylabel("Recall: share of anomalous\nwindows that are flagged")
    ax.xaxis.set_major_formatter(PercentFormatter(xmax=1, decimals=0))
    ax.yaxis.set_major_formatter(PercentFormatter(xmax=1, decimals=0))
    ax.grid(True)
    ax.legend(
        handles=[
            plt.Line2D(
                [],
                [],
                marker="o",
                linestyle="",
                color=BLUE,
                markersize=8,
                label="Approach A: LogBERT",
            ),
            plt.Line2D(
                [],
                [],
                marker="o",
                linestyle="",
                color=ORANGE,
                markersize=8,
                label="Approach B: rule-guided LLM",
            ),
            plt.Line2D([], [], color=AXIS, linewidth=1.0, label="Points with equal F1"),
        ],
        loc="lower left",
        bbox_to_anchor=(0, 1.03),
        ncol=3,
        borderaxespad=0,
    )
    save(fig, out, "07_results_pr_space")
    data["results"] = (
        table[["name", "precision", "recall", "f1", "fp", "inference_seconds"]]
        .round(4)
        .to_dict(orient="index")
    )


# 8. How many test templates each Approach B condition flags, and how many of
# those really contain alerts.
def fig_flagged_templates(output_dir: Path, out: Path, data: dict) -> None:
    conditions = [
        ("llm_zero_shot", "B1 zero-shot"),
        ("llm_few_shot", "B2 few-shot"),
        ("llm_rule_based", "B3 rule-based"),
        ("keyword_rules", "B4 keyword rules"),
    ]
    rows = []
    for method, name in conditions:
        decisions = pd.read_csv(output_dir / "rule_guided" / f"{method}_decisions.csv")
        test = decisions[decisions["n_test"] > 0]
        flagged = test[test["label"] == 1]
        with_alerts = int((flagged["n_test_anomalous"] > 0).sum())
        rows.append((name, with_alerts, len(flagged) - with_alerts))
    any_alert = int((test["n_test_anomalous"] > 0).sum())  # the same for every condition

    fig, ax = new_axes(9, 3.3, left=1.5, right=2.2, top=0.55)
    ax.set_xlim(0, max(a + n for _, a, n in rows) * 1.02)
    ax.set_ylim(-0.6, len(rows) - 0.4)
    for i, (_name, alerts, normal) in enumerate(rows):
        y = len(rows) - 1 - i
        rounded_bar(ax, 0, alerts, y, 0.5, ORANGE, horizontal=True, round_end=normal == 0)
        rounded_bar(ax, alerts, alerts + normal, y, 0.5, BLUE, horizontal=True)
        # Start the label after the reference line so the line never crosses it.
        ax.text(
            max(alerts + normal, any_alert) + 4,
            y,
            f"{alerts + normal} flagged, {alerts} with alerts",
            va="center",
            color=INK_2,
            fontsize=9,
        )
    ax.axvline(any_alert, color=INK_2, linewidth=1.0, zorder=3)
    ax.text(
        any_alert + 3,
        len(rows) - 0.48,
        f"{any_alert} test templates contain alerts",
        color=INK_2,
        fontsize=9,
        va="bottom",
    )
    ax.set_yticks([len(rows) - 1 - i for i in range(len(rows))], [r[0] for r in rows])
    ax.set_xlabel("Test templates labelled abnormal (of 472)")
    ax.grid(axis="x")
    ax.spines["left"].set_visible(False)
    ax.legend(
        handles=[
            Rectangle((0, 0), 1, 1, color=ORANGE, label="Template contains alerts (correct)"),
            Rectangle((0, 0), 1, 1, color=BLUE, label="Template is normal (false alarm)"),
        ],
        loc="lower left",
        bbox_to_anchor=(0, 1.05),
        ncol=2,
        borderaxespad=0,
    )
    save(fig, out, "08_llm_flagged_templates")
    data["flagged_templates"] = {
        "templates_with_alerts": any_alert,
        "conditions": {name: {"with_alerts": a, "normal": n} for name, a, n in rows},
    }


# 9. The keyword rules' F1 as a function of how many ranked rules they use.
def fig_rule_sensitivity(output_dir: Path, out: Path, data: dict, reported: int) -> None:
    table = pd.read_csv(output_dir / "analysis" / "keyword_rule_sensitivity.csv")
    best_k = int(table.loc[table["val_f1"].idxmax(), "k"])
    fig, ax = new_axes(8, 3.6, right=1.7, top=0.3)
    ax.set_xlim(0, table["k"].max() + 3)
    ax.set_ylim(0.6, 1.0)
    for column, color, name in (("val_f1", BLUE, "Validation F1"), ("test_f1", ORANGE, "Test F1")):
        ax.plot(
            table["k"],
            table[column],
            color=color,
            linewidth=2,
            marker="o",
            markersize=7,
            markeredgecolor=SURFACE,
            markeredgewidth=GAP_PT,
            label=name,
            zorder=3,
        )
        ax.text(
            table["k"].iloc[-1] + 2.5,
            table[column].iloc[-1],
            name,
            va="center",
            color=INK_2,
            fontsize=9,
        )
    # One marker line per k of interest; the labels sit on opposite sides.
    for k, text, side in (
        (reported, f"k = {reported}: reported\n(fixed in the proposal)", -1),
        (best_k, f"k = {best_k}: best\non validation", 1),
    ):
        ax.axvline(k, color=AXIS, linewidth=1.0, zorder=1)
        ax.text(
            k + side * 1.2,
            0.615,
            text,
            color=INK_2,
            fontsize=8.5,
            va="bottom",
            ha="left" if side > 0 else "right",
        )
    ax.set_xlabel("Number of top-ranked rules the keyword matcher uses (k)")
    ax.set_ylabel("F1 (window level)")
    ax.grid(axis="y")
    ax.legend(loc="lower right", bbox_to_anchor=(1.0, 0.18))
    save(fig, out, "09_keyword_rule_sensitivity")
    data["rule_sensitivity"] = table.round(4).to_dict(orient="list") | {"best_k": best_k}


# 10. What each method costs at inference time, on a logarithmic scale.
def fig_cost(output_dir: Path, out: Path, data: dict) -> None:
    table = pd.read_csv(output_dir / "results" / "comparison.csv")
    fig, ax = new_axes(8, 3.4, left=2.1, right=0.6, top=0.3)
    ticks = [0.001, 0.01, 0.1, 1, 10, 60, 600, 3600]
    ax.set_xscale("log")
    ax.set_xlim(0.0006, 9000)
    ax.set_ylim(-0.6, len(table) - 0.4)
    for i, row in table.iterrows():
        y = len(table) - 1 - i
        color = BLUE if row["method"].startswith("logbert") else ORANGE
        ax.plot(
            row["inference_seconds"],
            y,
            "o",
            markersize=9,
            color=color,
            markeredgecolor=SURFACE,
            markeredgewidth=GAP_PT,
            zorder=3,
        )
        ax.text(
            row["inference_seconds"] * 1.6,
            y,
            duration(row["inference_seconds"]),
            va="center",
            color=INK_2,
            fontsize=9,
        )
    ax.set_xticks(ticks, ["1 ms", "10 ms", "0.1 s", "1 s", "10 s", "1 min", "10 min", "1 h"])
    ax.minorticks_off()
    ax.set_yticks(
        [len(table) - 1 - i for i in range(len(table))],
        [SHORT_NAMES[m] for m in table["method"]],
    )
    ax.set_xlabel("Time to score the 5,742 test windows on 4 CPU cores (log scale)")
    ax.grid(axis="x")
    ax.spines["left"].set_visible(False)
    save(fig, out, "10_inference_cost")
    data["cost_seconds"] = dict(zip(table["method"], table["inference_seconds"], strict=True))


# ------------------------------------------------------------------ main


# Parse command-line arguments for this script.
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    add_config_arguments(parser)
    parser.add_argument("--out", default="docs/figures", help="Directory for the PNG files")
    parser.add_argument(
        "--skip-model",
        action="store_true",
        help="Skip the figure that runs LogBERT again (about a minute on a CPU)",
    )
    return parser.parse_args()


# Entry point: draw every figure and write the numbers behind them.
def main() -> None:
    args = parse_args()
    config = config_from_args(args)
    setup_style()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    output_dir = Path(config.paths.output_dir)
    logs = load_processed(
        config.paths.processed_dir, message_columns=["timestamp", "template_id", "label", "split"]
    )
    data: dict[str, Any] = {"config": args.config, "output_dir": str(output_dir)}

    print("Data and drift")
    fig_timeline(logs, out, data)
    fig_unseen_share(logs, out, data)
    print("Approach A: LogBERT")
    fig_training_curve(output_dir, out, data)
    fig_tuning_grid(output_dir, out, data)
    if not args.skip_model:
        g = json.loads((output_dir / "results" / "logbert.json").read_text())["settings"]["g"]
        fig_key_misses(key_ranks(config, logs), g, out, data)
    fig_window_scores(output_dir, logs, out, data)
    print("Results and Approach B")
    fig_pr_space(output_dir, out, data)
    fig_flagged_templates(output_dir, out, data)
    fig_rule_sensitivity(output_dir, out, data, config.rules.top_k)
    fig_cost(output_dir, out, data)

    path = out / "figure_data.json"
    path.write_text(json.dumps(data, indent=1, default=str) + "\n", encoding="utf-8")
    print(f"  wrote {path}")


if __name__ == "__main__":
    main()
