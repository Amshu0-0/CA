# Results: full BGL, run 2 on a GPU (Google Colab, Tesla T4)

Artifacts of the second run of the experiment, made by `notebooks/train_on_gpu.ipynb` with
`configs/bgl_gpu.yaml` and collected here by that notebook. The data, the split, the windows and the
LogBERT architecture are those of the run in `results/` (`configs/bgl_cpu.yaml`). What differs is the
amount of LogBERT training, which is the one thing that the CPU run had to cut down:

| | `results/` (run 1, 4-core CPU) | `results_gpu/` (run 2, Tesla T4) |
|---|---|---|
| LogBERT training chunks | duplicates dropped (12,394 distinct sequences) | every chunk kept (35,035) |
| Samples for the hypersphere centre | 4,000 | 20,000 |
| Epochs, patience | 6, 3 (kept epoch 5, best dev loss 1.02) | up to 50, 5 (stopped at epoch 22, kept epoch 17, best dev loss 0.79) |
| LogBERT training time | 60 minutes | 42 minutes (116 s per epoch) |
| Settings chosen on validation | A: g = 12, r = 3; A+: g = 8, r = 8 | A: g = 12, r = 3; A+: g = 20, r = 4 |

## Test period, window level

5,742 windows, of which 495 (8.6%) are anomalous. Inference time: scoring the test windows on the T4.

| Method | Precision | Recall | F1 | F1 in run 1 | FP | FN | Inference |
|---|---:|---:|---:|---:|---:|---:|---:|
| A: LogBERT | 0.175 | 0.921 | 0.294 | 0.293 | 2,149 | 39 | 4.9 s |
| A+: LogBERT, full-coverage masking | 0.162 | 0.952 | 0.276 | 0.305 | 2,445 | 24 | 10 s |

## How to read it next to `results/`

- LogBERT is unchanged: test F1 0.294 against 0.293, with 22 epochs instead of 6 and every training chunk.
  A hidden key of a template that is new to the model is missed every time in both runs
  (`figures/05_logbert_key_misses.png`), so more training does not remove the false alarms.
- Full-coverage masking (A+) moved in the other direction from run 1 (F1 0.305 there, 0.276 here). The
  checkpoint is different and so are the settings chosen on validation (g = 20, r = 4 here, g = 8, r = 8 in
  run 1). It is not a reliable improvement.
- An epoch took 116 s on the T4 over all 35,035 chunks, against about 600 s on the CPU over 12,394 distinct
  sequences; with 22 epochs the total was 42 minutes.

## Files

The layout is that of `results/` (see its README), with these differences:

| Path | Note |
|---|---|
| `run_info.json` | the GPU, the library versions and the time of the notebook's last session |
| `figures/` | the seven figures of `scripts/make_figures.py` for this run. Figure 3 (`03_logbert_training.png`) was redrawn: the first version cut the dev-loss curve off at the top because its y-axis was sized from the training loss only; the script is fixed |
