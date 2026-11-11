"""Train LogBERT (Approach A) on normal windows from the training period.

Usage:
    python scripts/train_logbert.py                                  # full BGL
    python scripts/train_logbert.py --config configs/bgl_2k.yaml     # smoke test
    python scripts/train_logbert.py --set logbert.max_train_sequences=20000  # quick CPU run

Writes to <output_dir>/logbert/:
    model.pt         best checkpoint (weights, vocabulary, hypersphere centre)
    history.json     per-epoch losses for the learning-curve plot in the report
    config.json      the exact configuration used
"""

from __future__ import annotations

import argparse
from pathlib import Path

from logad.config import add_config_arguments, config_from_args, config_to_dict
from logad.data.processed import load_processed
from logad.logbert.trainer import (
    LogBERTTrainer,
    build_model,
    prepare_training_data,
    resolve_device,
)
from logad.utils import Timer, save_json, set_seed, setup_logging


# Parse command-line arguments for this script.
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    add_config_arguments(parser)
    return parser.parse_args()


# Entry point: prepare data, train with early stopping, save the artifacts.
def main() -> None:
    setup_logging()
    config = config_from_args(parse_args())
    set_seed(config.seed)
    out_dir = Path(config.paths.output_dir) / "logbert"

    # LogBERT only needs template IDs, labels and splits - skip the text.
    logs = load_processed(
        config.paths.processed_dir, message_columns=["template_id", "label", "split"]
    )
    data = prepare_training_data(logs, config.logbert, seed=config.seed)

    device = resolve_device(config.logbert.device)
    model = build_model(len(data.vocab), config.logbert)
    n_params = sum(p.numel() for p in model.parameters())
    print(f"LogBERT: {n_params:,} parameters on {device}")

    trainer = LogBERTTrainer(model, config.logbert, device, seed=config.seed)
    with Timer() as timer:
        history = trainer.fit(data, out_dir / "model.pt")

    save_json(history, out_dir / "history.json")
    save_json(config_to_dict(config), out_dir / "config.json")
    best = min(history, key=lambda record: record["dev_loss"])
    print(
        f"Trained {len(history)} epochs in {timer.seconds / 60:.1f} min; "
        f"best dev loss {best['dev_loss']:.4f} at epoch {best['epoch']}. "
        f"Checkpoint: {out_dir / 'model.pt'}"
    )


if __name__ == "__main__":
    main()
