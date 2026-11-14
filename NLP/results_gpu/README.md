# Results: full BGL, run 2 on a GPU (Google Colab, Tesla T4)

Artifacts of the second run of the experiment, made by `notebooks/train_on_gpu.ipynb` with
`configs/bgl_gpu.yaml` and collected here by that notebook. The data, the split, the windows and the
LogBERT architecture are those of the run in `results/` (`configs/bgl_cpu.yaml`). What differs is the
amount of LogBERT training, the way the language model was run, and the rules it wrote:

| | `results/` (run 1, 4-core CPU) | `results_gpu/` (run 2, Tesla T4) |
|---|---|---|
| LogBERT training chunks | duplicates dropped (12,394 distinct sequences) | every chunk kept (35,035) |
| Samples for the hypersphere centre | 4,000 | 20,000 |
| Epochs, patience | 6, 3 (kept epoch 5, best dev loss 1.02) | up to 50, 5 (stopped at epoch 22, kept epoch 17, best dev loss 0.79) |
| LogBERT training time | 60 minutes | 42 minutes (116 s per epoch) |
| Language model | Qwen2.5-7B-Instruct, official GGUF `q4_k_m`, llama-cpp-python, CPU | Qwen2.5-7B-Instruct, 4-bit with bitsandbytes, transformers, GPU |
| Rules written | 160 candidates, 99 kept | 130 candidates, 54 kept |
| Settings chosen on validation | A: g = 12, r = 3; A+: g = 8, r = 8 | A: g = 12, r = 3; A+: g = 20, r = 4 |

## Test period, window level

5,742 windows, of which 495 (8.6%) are anomalous. Inference time: scoring the test windows (A) or
classifying the 472 test templates (B), on the T4.

| Method | Precision | Recall | F1 | FP | FN | Msg-level F1 | Inference |
|---|---:|---:|---:|---:|---:|---:|---:|
| A: LogBERT | 0.175 | 0.921 | 0.294 | 2,149 | 39 | - | 4.9 s |
| A+: LogBERT, full-coverage masking | 0.162 | 0.952 | 0.276 | 2,445 | 24 | - | 10 s |
| B1: LLM zero-shot | 0.105 | 1.000 | 0.190 | 4,219 | 0 | 0.283 | 15 min |
| B2: LLM few-shot | 0.439 | 0.939 | 0.598 | 595 | 30 | 0.512 | 16 min |
| B3: LLM rule-based | 0.416 | 0.992 | 0.586 | 690 | 4 | 0.504 | 26 min |
| B4: Keyword rules (no LLM) | 0.889 | 0.954 | 0.920 | 59 | 23 | 0.708 | 19 ms |

## How to read it next to `results/`

- LogBERT is unchanged: test F1 0.294 against 0.293, with 22 epochs instead of 6. A hidden key of a
  template that is new is missed every time in both runs (`figures/05_logbert_key_misses.png`).
- The keyword rules (B4) are still the best, but not without false alarms: all 59 come from four
  normal templates that contain the word `kernel` (rule 3 has the keywords `kernel` and `panic`).
- B2 and B3 changed places (0.481 and 0.644 in run 1, 0.598 and 0.586 here). The model wrote other
  rules, and the 4-bit runtime differs, so differences of about 0.1 between the language-model
  conditions should not be trusted.
- Which alert types the rules cover changes: KERNSOCK is found by B3 and B4 here (recall 1.00), and
  was not in run 1 (0.09 and 0.00).

## Files

The layout is that of `results/` (see its README), with these differences:

| Path | Note |
|---|---|
| `run_info.json` | the GPU, the library versions and the time of the notebook's last session |
| `figures/` | the ten figures of `scripts/make_figures.py`. Figure 3 (`03_logbert_training.png`) was redrawn: the first version cut the dev-loss curve off at the top because its y-axis was sized from the training loss only; the script is fixed |
| `llm_cache/` | not included here; the prompts and raw answers of the run are in the Google Drive folder `CA-NLP-run/llm_cache/` of the notebook |
