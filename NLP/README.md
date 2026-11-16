# LogBERT for System Log Anomaly Detection: a Reproduction on the BGL Supercomputer Log

[![tests](https://github.com/Amshu0-0/CA/actions/workflows/tests.yml/badge.svg)](https://github.com/Amshu0-0/CA/actions/workflows/tests.yml)

CSCI 390 - Natural Language Processing and Applications · Instructor: Dr. Qi Li
Project type: research reproduction and extension (closest to Option 1, Text Classification, applied to system logs)

**Research question.** How well does LogBERT, a BERT-style model that learns which *sequences of log-template
IDs* are normal, find the anomalous 5-minute windows of a real supercomputer log when it has to predict the
future from the past?

| | LogBERT (reproduction) |
|---|---|
| Paper | Guo et al., 2021 - *LogBERT: Log Anomaly Detection via BERT* |
| Reads | sequences of Drain template IDs ("log keys") in a window; it never reads the words of a message |
| Learns from | **normal** training windows only (self-supervised: masked key prediction plus a hypersphere loss) |
| Decides | a window is anomalous if more than *r* of its hidden keys are not among the model's top-*g* guesses |

The data is the BGL log (Blue Gene/L, 4,747,963 messages over about seven months). Windows are 5 minutes long
and slide by 1 minute; the split is **chronological, 60/20/20** (train on the past, validate and test on the
future). *g* and *r* are tuned on the validation period only, and every number below is from the test period.

- Run 1 (4-core CPU): results and trained model in [`results/`](results/README.md); every number and figure of it
  embedded in [`notebooks/results_reported_run.ipynb`](notebooks/results_reported_run.ipynb)
- Run 2 (the full training recipe on a free Colab GPU): [`notebooks/train_on_gpu.ipynb`](notebooks/train_on_gpu.ipynb),
  with its results in [`results_gpu/`](results_gpu/README.md)

## Results

**Run 1.** Full BGL, test period: 5,742 windows, of which 495 are anomalous (8.6%). Run on a 4-core CPU with
`configs/bgl_cpu.yaml` (6 epochs, about 60 minutes of training).

| Code | Method | Precision | Recall | F1 | False positives | Inference |
|---|---|---:|---:|---:|---:|---:|
| A | LogBERT, original random masking | 0.174 | 0.921 | 0.293 | 2,159 | 78 s |
| A+ | LogBERT with full-coverage masking (our extension: every key is checked once) | 0.184 | 0.897 | 0.305 | 1,975 | 156 s |

**Run 2: the full recipe on a free GPU.** The same data, split, windows and architecture, run again on a Colab
Tesla T4 with every training chunk kept (duplicates included), up to 50 epochs with early stopping (it stopped
after 22) and 20,000 sequences for the hypersphere centre. Notebook:
[`notebooks/train_on_gpu.ipynb`](notebooks/train_on_gpu.ipynb). Files: [`results_gpu/`](results_gpu/README.md).

| Code | Method | Precision | Recall | F1 | F1 in run 1 | False positives | Inference |
|---|---|---:|---:|---:|---:|---:|---:|
| A | LogBERT, original random masking | 0.175 | 0.921 | 0.294 | 0.293 | 2,149 | 4.9 s |
| A+ | LogBERT with full-coverage masking | 0.162 | 0.952 | 0.276 | 0.305 | 2,445 | 10 s |

### What the numbers say

- **LogBERT finds almost every anomalous window and raises many false alarms.** In run 1 it flags 456 of the 495
  anomalous windows (recall 0.921) but also 2,159 of the 5,247 normal ones (41%), so precision is 0.174.
- **New templates are the cause.** BGL changes over time: 79.6% of the messages of the test period (8.9% of the
  validation period) belong to a template that never occurs in the training period
  ([`docs/figures/02_unseen_templates.png`](docs/figures/02_unseen_templates.png)). In run 1, a key that LogBERT
  has never seen in a normal training window is read as `<unk>` and missed every time (100% of 360,557 normal and
  28,094 alert keys), while a known key is missed 1.9% (normal) and 0.2% (alert) of the time
  ([`05_logbert_key_misses.png`](docs/figures/05_logbert_key_misses.png)). Normal windows whose templates were all
  seen in training are flagged 0.3% of the time (4 of 1,514); normal windows with a template that was never seen
  are flagged 57.7% of the time (2,155 of 3,733). So 2,155 of the 2,159 false positives, 99.8%, contain such
  a template ([`06_logbert_window_scores.png`](docs/figures/06_logbert_window_scores.png)). Run 2 shows the same
  pattern: new keys missed 100% of the time, known keys 2.1% (normal) and 0.6% (alert).
- **It was not under-trained.** Run 2 trained 22 epochs instead of 6 and with every chunk, and plain LogBERT's
  test F1 is the same (0.294 against 0.293). More training does not fix what the model has never seen.
- **Full-coverage masking is not a reliable gain.** It changed F1 by +0.012 in run 1 and by -0.018 in run 2, so
  it is reported as an extension, not as an improvement.
- **Which alerts it misses.** Per alert type (run 1, window level), the large categories are found almost
  every time (APPSEV 0.977, KERNRTSP 0.994, KERNMC 0.979, MASABNORM 0.984); the weak ones are KERNSOCK (recall
  0.391 on 23 windows) and LINKIAP (0.143 on 7). The template most often behind a missed window is
  `<*> <*> <*> socket closed` (14 windows). See [`results/analysis/analysis.md`](results/analysis/analysis.md).

| | |
|---|---|
| [`01_bgl_timeline.png`](docs/figures/01_bgl_timeline.png) | normal and alert messages per week |
| [`02_unseen_templates.png`](docs/figures/02_unseen_templates.png) | share of messages whose template never occurs in training, per week |
| [`03_logbert_training.png`](docs/figures/03_logbert_training.png) | training and dev loss per epoch |
| [`04_logbert_tuning_grid.png`](docs/figures/04_logbert_tuning_grid.png) | validation F1 for every (g, r) pair |
| [`05_logbert_key_misses.png`](docs/figures/05_logbert_key_misses.png) | share of hidden keys the model misses, known against new templates |
| [`06_logbert_window_scores.png`](docs/figures/06_logbert_window_scores.png) | window score of normal and anomalous windows |
| [`07_results_pr_space.png`](docs/figures/07_results_pr_space.png) | precision and recall of both variants |

`docs/figures/` is drawn from run 1; [`results_gpu/figures/`](results_gpu/figures) has the same seven for run 2.

## How LogBERT works here

1. **Drain** turns every message into a template with `<*>` for the variable parts, e.g.
   `ciod: LOGIN <*> failed: <*> <*>`. The template's number is its *log key*. The BGL alert tag (the first field of
   a line, which says whether the message is an alert) is split off while reading and never reaches the model.
2. A **window** is the sequence of keys of 5 minutes of messages. A window is anomalous if it contains an alert.
   Windows longer than the model (511 keys) are split into chunks instead of truncated.
3. The **model** is a small Transformer encoder trained from scratch: 2 pre-norm layers, 4 heads, hidden size
   256, feed-forward size 1,024 (GELU), 1,670,320 parameters. Its vocabulary is 4 special tokens plus the 172 keys
   of the normal training windows; a key outside it is `<unk>`.
4. **Training** uses two self-supervised tasks on normal windows only, with loss = MLKP + 0.1 × VHM:
   *masked log key prediction* (hide half of the keys: 80% become `<mask>`, 10% a random key, 10% stay; predict
   them from context) and *volume of hypersphere minimisation* (pull the representation of each normal sequence,
   the output at the `<dist>` token, towards a common centre that is re-estimated every epoch).
5. **Detection.** Hide keys, rank the model's guesses, and count the hidden keys whose true key is outside the top
   *g*. A chunk is anomalous if it has more than *r* such misses, and a window if any chunk is. *g* and *r* are
   chosen on the validation period (a grid of 11 values of *g* by 21 values of *r*).
   - **A** hides a random half of the keys once, as in the paper.
   - **A+** (our extension) hides every key exactly once, in two disjoint halves, so no key is skipped.

## Repository layout

```
configs/            experiment settings (bgl.yaml = full BGL, bgl_cpu.yaml = run 1 on a CPU,
                    bgl_gpu.yaml = run 2 on a Colab T4, bgl_2k.yaml = smoke test)
src/logad/
  config.py         typed YAML config with `extends:` and --set overrides
  data/             download, BGL parsing (alert tag removed), Drain, chronological split, windows
  logbert/          vocabulary, masked dataset, model, trainer, detector
  evaluation/       metrics, prediction files, comparison table, error analysis
  demo.py           UI-free back end of the demo
scripts/            one command-line entry point per pipeline stage
app/demo.py         Gradio demo
notebooks/          train_on_gpu.ipynb (run 2 on a Colab GPU, results embedded),
                    results_reported_run.ipynb (run 1, results embedded),
                    colab_full_run.ipynb (first version of the Colab notebook, superseded)
tests/              101 unit and integration tests (offline)
docs/figures/       the figures of run 1 (and the data behind them)
results/            run 1: metrics, LogBERT checkpoint, predictions, analysis
results_gpu/        run 2 (GPU): the same, plus figures
```

## Setup

Python 3.10 or newer.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

`requirements.txt` installs this package in editable mode with all extras (`demo`, `dev`, `docs`). For a
lighter install, use `pip install -e ".[dev]"`. On a machine without a GPU you can avoid the large
CUDA wheels by installing PyTorch first:

```bash
pip install torch --index-url https://download.pytorch.org/whl/cpu
```

## Quick check: the whole pipeline in about a minute

```bash
make smoke
```

This downloads Loghub's 2,000-line BGL sample, preprocesses it, trains a tiny LogBERT on the CPU,
tunes and evaluates both variants, and writes the comparison and analysis to `outputs/bgl_2k/`. The sample is
spread over seven months, so its windows are nearly empty and **its numbers mean nothing**; it only
checks that everything runs.

## Full experiment

Run the stages with `make all` (after `make data`) or follow along below. Each script accepts `--config` and any
number of `--set section.key=value` overrides. A GPU is strongly recommended; the notebook
`notebooks/train_on_gpu.ipynb` does everything on a free Colab T4 (run 2).

```bash
python scripts/download_data.py      # full BGL from Loghub (Zenodo), ~700 MB unpacked
python scripts/preprocess.py         # templates, split and windows -> data/processed/bgl/
```

```bash
python scripts/train_logbert.py
python scripts/evaluate_logbert.py                                            # A
python scripts/evaluate_logbert.py --set logbert.detection.full_coverage=true # A+
```

```bash
python scripts/compare.py            # -> outputs/bgl/results/comparison.{md,csv}
python scripts/analyze.py            # -> outputs/bgl/analysis/analysis.md
python scripts/make_figures.py       # -> docs/figures/
make results                         # copy the deliverables into results/ for git
```

With `configs/bgl.yaml` the training recipe is the full one of the paper (up to 50 epochs); on a CPU
use `--config configs/bgl_cpu.yaml` (the reported run 1: duplicate sequences dropped, 6 epochs, about an
hour on 4 cores), on a GPU use `--config configs/bgl_gpu.yaml` (run 2). Pass the same `--config` to every script.

## Live demo

```bash
python app/demo.py            # http://127.0.0.1:7860
python app/demo.py --share    # temporary public link for the class
```

To demo the reported run without retraining anything, preprocess the data, then put the committed
model and results back in place and point the demo at that run:

```bash
make data preprocess
```

```bash
make restore OUT=outputs/bgl_cpu
```

```bash
python app/demo.py --config configs/bgl_cpu.yaml --share
```

- **Paste a log line.** A full BGL line has its alert tag removed (shown as ground truth). The line is
  matched to its Drain template and inserted into a normal test window. You then see LogBERT's verdict:
  how many hidden keys fell outside the top-*g*, the rank of your line's key, and the count without your line.
- **Insert into a window.** Choose a normal test window and insert any message at any position.

If the trained model is missing, the demo says "not available" instead of failing.

## Design decisions

- **Label leakage.** BGL's first field is the alert tag. It is split off while reading, and the model only
  ever sees message content (tested).
- **Chronological split.** No shuffling (Le & Zhang, 2022): the model is trained on the past and tested on the
  future. `compare.py` recomputes every metric from the saved predictions and rejects mismatched windows.
- **Faithful but careful LogBERT.** The paper's architecture and objective are kept. The detection
  settings are tuned on validation rather than test; every test window is evaluated, including short
  ones; and long windows are chunked rather than truncated.
- **Extension.** Full-coverage masking checks every key exactly once instead of a random half.
- **Training from scratch.** There are no pretrained weights; LogBERT here is a small Transformer over keys, not a
  language model that reads words.

## Development

```bash
make test        # pytest (offline, a few seconds)
make lint        # ruff check + ruff format --check
make help        # every pipeline target
```

GitHub Actions runs lint and tests on Python 3.10 and 3.12 for every push.

## Deliverables checklist

| Course requirement | Where |
|---|---|
| Documented, reproducible code | `src/logad/`, `scripts/`, `configs/`, `Makefile`, tests and CI |
| Trained models | `results/logbert/model.pt`, `results_gpu/logbert/model.pt` |
| Results and error analysis | `results/comparison.md`, `results/analysis/analysis.md`, `docs/figures/` |
| Run instructions | this README, `results/README.md`, `notebooks/train_on_gpu.ipynb` |
| Interactive demo | `app/demo.py` |

## References

- Guo, H., Yuan, S., & Wu, X. (2021). LogBERT: Log anomaly detection via BERT. arXiv:2103.04475.
- He, P., Zhu, J., Zheng, Z., & Lyu, M. R. (2017). Drain: An online log parsing approach with fixed depth tree. ICWS 2017.
- Le, V.-H., & Zhang, H. (2022). Log-based anomaly detection with deep learning: How far are we? ICSE 2022.
- Oliner, A., & Stearley, J. (2007). What supercomputers say: A study of five system logs. DSN 2007.
- Zhu, J., He, S., He, P., Liu, J., & Lyu, M. R. (2023). Loghub: A large collection of system log datasets for AI-driven log analytics. ISSRE 2023.
