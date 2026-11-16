# Results: full BGL, run 1 on a 4-core CPU

Artifacts of the run produced with `configs/bgl_cpu.yaml` on the full Loghub BGL log (4,747,963 messages)
and copied here by `make results OUT=outputs/bgl_cpu`. Every number of it, with the code that made it, is also
embedded in `notebooks/results_reported_run.ipynb`.

## Test period, window level

5,742 windows, of which 495 (8.6%) are anomalous. Inference time: scoring the test windows on 4 CPU cores.

| Method | Precision | Recall | F1 | FP | FN | Inference |
|---|---:|---:|---:|---:|---:|---:|
| A: LogBERT | 0.174 | 0.921 | 0.293 | 2,159 | 39 | 78 s |
| A+: LogBERT, full-coverage masking | 0.184 | 0.897 | 0.305 | 1,975 | 51 | 156 s |

Detection settings, chosen on the 10,478 validation windows (446 anomalous) and then applied unchanged to the
test windows:

| Method | g | r | Validation precision | Validation recall | Validation F1 |
|---|---:|---:|---:|---:|---:|
| A | 12 | 3 | 0.471 | 0.823 | 0.599 |
| A+ | 8 | 8 | 0.488 | 0.827 | 0.614 |

One-off cost: LogBERT training took 60 minutes (6 epochs of about 10 minutes each on the CPU). The checkpoint
kept is that of epoch 5, which had the lowest dev loss (1.02).

## Files

| Path | Contents |
|---|---|
| `comparison.md`, `comparison.csv` | the table above, recomputed from the saved predictions by `scripts/compare.py` |
| `logbert.json`, `logbert_full.json` | metrics of A and A+ on validation and test, and the (g, r) chosen on the validation period |
| `predictions/<method>.parquet` | each method's verdict on every test window (`pred`), plus LogBERT's window score (`score`: the most missed keys in any chunk) |
| `logbert/model.pt` | the trained LogBERT checkpoint (weights, vocabulary, hypersphere centre); load with `logad.logbert.trainer.load_checkpoint` |
| `logbert/history.json` | per-epoch training and dev losses |
| `logbert/*_tuning_grid.csv` | validation precision, recall and F1 for every (g, r) pair |
| `analysis/` | error analysis: `analysis.md` plus one CSV per table (recall per alert category, agreement between A and A+, alert templates in missed windows) |

The figures of this run are in `docs/figures/` (drawn by `scripts/make_figures.py`).

## Re-scoring the model without retraining

The checkpoint and the tuned settings are committed, so the demo and the evaluation can be run without
training anything. The preprocessed data is needed first, because the windows are rebuilt from it
(`make data` and `make preprocess`).

```bash
make restore OUT=outputs/bgl_cpu
```

This copies this folder back to `outputs/bgl_cpu/`. Then, to score the test windows again with the committed
checkpoint:

```bash
python scripts/evaluate_logbert.py --config configs/bgl_cpu.yaml
python scripts/evaluate_logbert.py --config configs/bgl_cpu.yaml --set logbert.detection.full_coverage=true
python scripts/compare.py --config configs/bgl_cpu.yaml
```

The masks used at detection time are seeded, so the same checkpoint gives the same predictions on the same
hardware. To start the demo on this run: `python app/demo.py --config configs/bgl_cpu.yaml`.
