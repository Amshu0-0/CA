"""Tune LogBERT's detection settings on validation windows and evaluate on test windows.

Usage:
    python scripts/evaluate_logbert.py
    python scripts/evaluate_logbert.py --set logbert.detection.full_coverage=true   # extension

Steps:
  1. score every validation window once (rank of each masked key)
  2. grid-search g (top-g candidates) and r (allowed misses) for the best F1
  3. score the test windows, apply the chosen (g, r) and compute metrics

Writes predictions/<method>.parquet, results/<method>.json and
logbert/<method>_tuning_grid.csv under the output directory, where <method>
is "logbert" (original masking) or "logbert_full" (full-coverage masking).
"""

from __future__ import annotations

import argparse
from pathlib import Path

from logad.config import add_config_arguments, config_from_args
from logad.data.processed import load_processed
from logad.evaluation.metrics import binary_metrics
from logad.evaluation.predictions import save_method_result, save_window_predictions
from logad.logbert.detector import LogBERTDetector, tune_thresholds
from logad.logbert.trainer import resolve_device
from logad.utils import set_seed, setup_logging


# Parse command-line arguments for this script.
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    add_config_arguments(parser)
    return parser.parse_args()


# Entry point: tune on validation, evaluate on test, save everything.
def main() -> None:
    setup_logging()
    config = config_from_args(parse_args())
    set_seed(config.seed)
    detection = config.logbert.detection
    method = "logbert_full" if detection.full_coverage else "logbert"
    output_dir = Path(config.paths.output_dir)

    logs = load_processed(
        config.paths.processed_dir, message_columns=["template_id", "label", "split"]
    )
    device = resolve_device(config.logbert.device)
    detector = LogBERTDetector.from_checkpoint(
        output_dir / "logbert" / "model.pt", detection, device
    )

    # 1-2. Tune (g, r) on the labeled validation period.
    val_windows = logs.windows_in("val")
    val_scores = detector.score_windows(logs.template_ids, val_windows)
    choice = tune_thresholds(
        val_scores, val_windows["label"].to_numpy(), detection.g_values, detection.r_max
    )
    choice.grid.to_csv(output_dir / "logbert" / f"{method}_tuning_grid.csv", index=False)

    # 3. Apply the chosen settings to the test period.
    test_windows = logs.windows_in("test")
    test_scores = detector.score_windows(logs.template_ids, test_windows)
    predictions = test_scores.predict(choice.g, choice.r)
    test_metrics = binary_metrics(test_windows["label"].to_numpy(), predictions)

    save_window_predictions(
        output_dir,
        method,
        test_windows["window_id"].to_numpy(),
        predictions,
        scores=test_scores.max_misses(choice.g),
    )
    save_method_result(
        output_dir,
        method,
        {
            "approach": "A",
            "settings": {
                "g": choice.g,
                "r": choice.r,
                "mask_ratio": detection.mask_ratio,
                "full_coverage": detection.full_coverage,
            },
            "validation": choice.metrics.as_dict(),
            "test": test_metrics.as_dict(),
            "inference_seconds": test_scores.seconds,
            "n_test_windows": len(test_windows),
        },
    )

    print(f"\n{method}: tuned on validation -> g={choice.g}, r={choice.r}")
    for name, metrics in (("validation", choice.metrics), ("test", test_metrics)):
        print(
            f"  {name:<10} P={metrics.precision:.3f} R={metrics.recall:.3f} "
            f"F1={metrics.f1:.3f} (TP={metrics.tp} FP={metrics.fp} FN={metrics.fn})"
        )
    print(f"  test inference: {test_scores.seconds:.2f}s for {len(test_windows):,} windows")


if __name__ == "__main__":
    main()
