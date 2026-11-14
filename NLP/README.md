# Comparing BERT-Based and Rule-Guided Language Models for System Log Anomaly Detection

[![tests](https://github.com/Amshu0-0/CA/actions/workflows/tests.yml/badge.svg)](https://github.com/Amshu0-0/CA/actions/workflows/tests.yml)

CSCI 390 - Natural Language Processing and Applications · Instructor: Dr. Qi Li
Project type: research reproduction and extension (closest to Option 1, Text Classification, applied to system logs)

**Research question.** How does a BERT-style model that learns patterns in *sequences of log-template
IDs* compare with a *rule-guided large language model that reads the log text itself* in identifying
anomalous system events?

| | Approach A: LogBERT (reproduction) | Approach B: rule-guided LLM (partial LogRules adaptation) |
|---|---|---|
| Paper | Guo et al., 2021 - *LogBERT: Log Anomaly Detection via BERT* | Huang et al., 2025 - *LogRules* (Findings of NAACL 2025) |
| Reads | sequences of Drain template IDs in a window | the text of each unique log template |
| Learns from | **normal** training windows only (self-supervised) | rules an LLM induces from **labeled** training templates, verified on the training set |
| Decides | anomalous if more than *r* masked keys fall outside the top-*g* predictions | zero-shot, few-shot or rule-based LLM prompting, or plain keyword matching |

Both approaches are evaluated on **the same 5-minute test windows** of BGL, after a **chronological
60/20/20 split**, by **the same evaluation code**.

- Run 1 (4-core CPU): results, trained model, rules and LLM answers in [`results/`](results/README.md);
  every number and figure of it embedded in [`notebooks/results_reported_run.ipynb`](notebooks/results_reported_run.ipynb)
- Run 2 (the full recipe on a free Colab GPU): [`notebooks/train_on_gpu.ipynb`](notebooks/train_on_gpu.ipynb),
  with its results in [`results_gpu/`](results_gpu/README.md)

## Results

**Run 1.** Full BGL (4,747,963 messages), test period: 5,742 windows, of which 495 are anomalous. Run on a 4-core
CPU with `configs/bgl_cpu.yaml`; Approach B used Qwen2.5-7B-Instruct (4-bit) for both rule induction
and classification.

| Code | Method | Precision | Recall | F1 | False positives | Inference |
|---|---|---:|---:|---:|---:|---:|
| A | LogBERT, original random masking | 0.174 | 0.921 | 0.293 | 2,159 | 78 s |
| A+ | LogBERT with full-coverage masking (our extension: every key is checked once) | 0.184 | 0.897 | 0.305 | 1,975 | 156 s |
| B1 | LLM zero-shot | 0.108 | 1.000 | 0.194 | 4,103 | 47 min |
| B2 | LLM few-shot (case-based) | 0.323 | 0.941 | 0.481 | 976 | 53 min |
| B3 | LLM rule-based | 0.489 | 0.943 | 0.644 | 488 | 57 min |
| B4 | the same induced rules as plain keyword matching (no LLM) | 1.000 | 0.915 | **0.956** | 0 | 8 ms |

In short, the rule-guided approach wins clearly, and its rules win without the LLM:

- **LogBERT is hurt by new templates.** 361 of the 472 test templates never occur in training, and
  99.8% of LogBERT's false-positive windows contain one.
- **The LLM applies the rules loosely.** It follows the rules' sentences rather than their verified
  keywords: the cited rule's keyword occurs in the template in only 20 of 420 citations.

### Run 2: the full recipe on a free GPU

The same data, split, windows and models, run again on a Colab Tesla T4 with the full LogBERT recipe
(every training chunk kept, up to 50 epochs, 20,000 samples for the centre) and Qwen2.5-7B-Instruct
through `transformers` in 4 bits, which also wrote a new set of rules (54 verified, 99 in run 1).
Notebook: [`notebooks/train_on_gpu.ipynb`](notebooks/train_on_gpu.ipynb). Files: [`results_gpu/`](results_gpu/README.md).

| Code | Method | Precision | Recall | F1 | F1 in run 1 | False positives | Inference |
|---|---|---:|---:|---:|---:|---:|---:|
| A | LogBERT, original random masking | 0.175 | 0.921 | 0.294 | 0.293 | 2,149 | 4.9 s |
| A+ | LogBERT with full-coverage masking | 0.162 | 0.952 | 0.276 | 0.305 | 2,445 | 10 s |
| B1 | LLM zero-shot | 0.105 | 1.000 | 0.190 | 0.194 | 4,219 | 15 min |
| B2 | LLM few-shot (case-based) | 0.439 | 0.939 | 0.598 | 0.481 | 595 | 16 min |
| B3 | LLM rule-based | 0.416 | 0.992 | 0.586 | 0.644 | 690 | 26 min |
| B4 | the same induced rules as plain keyword matching (no LLM) | 0.889 | 0.954 | **0.920** | 0.956 | 59 | 19 ms |

- **LogBERT is unchanged** (F1 0.294 against 0.293) after 22 epochs instead of 6: it was not
  under-trained, new templates are the problem.
- **The rules still win without the LLM**, but not without false alarms: all 59 of B4 come from four
  normal templates that contain the word `kernel`, which one rule uses as a keyword.
- **The middle moves.** B2 and B3 swap places, because the model wrote other rules. Read the ends of
  the ranking (rules above the LLM above LogBERT), not differences of about 0.1 between the LLM conditions.

[`results/`](results/README.md) (run 1) and [`results_gpu/`](results_gpu/README.md) (run 2) have every
table, the trained model, the rules and the decisions. Run 1 also has all LLM answers; run 2's cache stays
in the Google Drive folder of the notebook.

## Repository layout

```
configs/            experiment settings (bgl.yaml = full BGL, bgl_cpu.yaml = run 1 on a CPU,
                    bgl_gpu.yaml = run 2 on a Colab T4, bgl_2k.yaml = smoke test)
prompts/            every LLM prompt, as plain text (induction + 3 classification conditions)
src/logad/
  config.py         typed YAML config with `extends:` and --set overrides
  data/             download, BGL parsing (alert tag removed), Drain, chronological split, windows
  logbert/          Approach A: vocabulary, masked dataset, model, trainer, detector
  rules/            Approach B: rule induction + verification, keyword baseline, deduction runner
  llm/              Approach B: OpenAI-compatible / transformers clients, response cache, parsing
  evaluation/       shared metrics, prediction files, comparison table, error analysis
  demo.py           UI-free back end of the demo
scripts/            one command-line entry point per pipeline stage
app/demo.py         Gradio demo
notebooks/          train_on_gpu.ipynb (run 2 on a Colab GPU, results embedded),
                    results_reported_run.ipynb (run 1, results embedded),
                    colab_full_run.ipynb (first version of the Colab notebook, superseded)
tests/              148 unit and integration tests (offline; stub LLMs only)
results/            run 1: metrics, LogBERT checkpoint, rules, decisions, analysis, LLM cache
results_gpu/        run 2 (GPU): the same, without the LLM cache
```

## Setup

Python 3.10 or newer.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

`requirements.txt` installs this package in editable mode with all extras (`llm`, `demo`, `dev`). For a
lighter install, use `pip install -e ".[dev]"`. On a machine without a GPU you can avoid the large
CUDA wheels by installing PyTorch first:

```bash
pip install torch --index-url https://download.pytorch.org/whl/cpu
```

## Quick check: the whole Approach A pipeline in about a minute

```bash
make smoke
```

This downloads Loghub's 2,000-line BGL sample, preprocesses it, trains a tiny LogBERT on the CPU,
tunes and evaluates it, and writes the comparison and analysis to `outputs/bgl_2k/`. The sample is
spread over seven months, so its windows are nearly empty and **its numbers mean nothing**; it only
checks that everything runs.

## Full experiment

Run every step with `make all` or follow along below. Each script accepts `--config` and any
number of `--set section.key=value` overrides. A GPU is strongly recommended; the notebook
`notebooks/train_on_gpu.ipynb` does everything on a free Colab T4 (run 2).

```bash
python scripts/download_data.py      # full BGL from Loghub (Zenodo), ~700 MB unpacked
python scripts/preprocess.py         # templates, split and windows -> data/processed/bgl/
```

**Approach A - LogBERT**

```bash
python scripts/train_logbert.py
python scripts/evaluate_logbert.py                                            # A
python scripts/evaluate_logbert.py --set logbert.detection.full_coverage=true # A+
```

**Approach B - rule-guided LLM.** Two models are configured in `configs/bgl.yaml`: the **generator**
(rule induction) and the **classifier**. **No API key is needed.** Both default to the open
Qwen2.5-7B-Instruct, which LogRules also used. LogRules induced its rules with GPT-4o-mini, and the
proposal allows a comparable model when that is unavailable. Any OpenAI-compatible server works for
either model. Three ways to run them:

1. **Locally with Ollama (default config).** Serve Qwen once; both roles use it:
   ```bash
   ollama pull qwen2.5:7b-instruct
   ```
   Then keep `ollama serve` running in another terminal. A GPU makes this much faster.
2. **Colab GPU.** Run Qwen in-process with `transformers`; the notebook does this for both roles.
   For example, for the classifier:
   ```bash
   python scripts/run_rule_guided.py \
       --set llm.classifier.backend=transformers \
       --set llm.classifier.model=Qwen/Qwen2.5-7B-Instruct \
       --set llm.classifier.load_in_4bit=true
   ```
   This needs `pip install bitsandbytes`. Use the same overrides on `llm.generator` for
   `induce_rules.py`.
3. **Optional: GPT-4o-mini for induction, as in LogRules.** With an OpenAI key:
   ```bash
   export OPENAI_API_KEY=...
   python scripts/induce_rules.py --set llm.generator.model=gpt-4o-mini \
       --set llm.generator.base_url=null --set llm.generator.api_key_env=OPENAI_API_KEY
   ```
4. **CPU only (the reported run).** Download the two `qwen2.5-7b-instruct-q4_k_m-*.gguf` files from
   the Hugging Face repository `Qwen/Qwen2.5-7B-Instruct-GGUF`, then serve them with llama.cpp's
   OpenAI-compatible server:
   ```bash
   pip install "llama-cpp-python[server]"
   ```
   ```bash
   python -m llama_cpp.server --model qwen2.5-7b-instruct-q4_k_m-00001-of-00002.gguf --model_alias qwen2.5:7b-instruct --n_ctx 8192 --n_threads 4 --port 8000
   ```
   Then pass `--config configs/bgl_cpu.yaml` to every script. It points both roles at that server and
   trims the run to fit a CPU: on 4 cores, LogBERT trains in about an hour, and each
   LLM condition takes about an hour.

```bash
python scripts/induce_rules.py       # -> outputs/bgl/rules/rules.{json,md}
python scripts/run_rule_guided.py    # B1-B4 (add --conditions keyword to run B4 alone)
```

**Compare and analyse**

```bash
python scripts/compare.py            # -> outputs/bgl/results/comparison.{md,csv}
python scripts/analyze.py            # -> outputs/bgl/analysis/analysis.md
make results                         # copy the deliverables into results/ for git
```

Every LLM prompt and response is cached in `outputs/llm_cache/*.jsonl`. Re-running costs nothing, and
the cache is a complete record of all LLM calls. The reported run's cache is committed in
`results/llm_cache/`, so its Approach B results replay without a model (`results/README.md`).

## Live demo

```bash
python app/demo.py            # http://127.0.0.1:7860
python app/demo.py --share    # temporary public link for the class
```

To demo the reported run without retraining anything, preprocess the data, then put the committed
model, rules and decisions back in place and point the demo at that run:

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
  matched to its Drain template and inserted into a normal test window. You then see LogBERT's verdict
  (missed keys, your line's rank, the count without your line), the keyword rule that fired, and the
  rule-based LLM's verdict with the rule it cited.
- **Insert into a window.** Choose a normal test window and insert any message at any position.

The demo works at every stage of the pipeline: methods whose artifacts are missing are marked "not
available". For known templates it uses the LLM decisions saved by the experiment, and it calls the
LLM live only for unseen lines.

## Design decisions

- **Label leakage.** BGL's first field is the alert tag. It is split off while reading, and models only
  ever see message content (tested).
- **Chronological split, shared windows.** No shuffling (Le & Zhang, 2022). Both approaches are scored
  on identical test windows, and `compare.py` recomputes every metric from the saved predictions and
  rejects mismatched windows.
- **Faithful but careful LogBERT.** The paper's architecture and objective are kept. The detection
  settings are tuned on validation rather than test; every test window is evaluated, including short
  ones; and long windows are chunked rather than truncated.
- **Extension.** Full-coverage masking checks every key exactly once instead of a random 50%.
- **LogRules, partially.** Induction and deduction are implemented; the CPO alignment (fine-tuning)
  stage is not. Rules are verified by keyword matching on the training set, so the same rules drive
  both the rule-based prompt and the keyword baseline.

## Development

```bash
make test        # pytest (offline, about 6 s)
make lint        # ruff check + ruff format --check
make help        # every pipeline target
```

GitHub Actions runs lint and tests on Python 3.10 and 3.12 for every push.

## Deliverables checklist

| Course requirement | Where |
|---|---|
| Documented, reproducible code | `src/logad/`, `scripts/`, `configs/`, `Makefile`, tests and CI |
| Trained models | `results/logbert/model.pt`, `results_gpu/logbert/model.pt` |
| Induced rules, all prompts and every LLM answer | `results/rules/rules.md`, `prompts/`, `results/llm_cache/` |
| Results and error analysis | `results/comparison.md`, `results/analysis/analysis.md` |
| Run instructions | this README, `results/README.md`, `notebooks/train_on_gpu.ipynb` |
| Interactive demo | `app/demo.py` |

## References

- Guo, H., Yuan, S., & Wu, X. (2021). LogBERT: Log anomaly detection via BERT. arXiv:2103.04475.
- He, P., Zhu, J., Zheng, Z., & Lyu, M. R. (2017). Drain: An online log parsing approach with fixed depth tree. ICWS 2017.
- Huang, X., Zhang, T., & Zhao, W. (2025). LogRules: Enhancing log analysis capability of large language models through rules. Findings of NAACL 2025.
- Le, V.-H., & Zhang, H. (2022). Log-based anomaly detection with deep learning: How far are we? ICSE 2022.
- Oliner, A., & Stearley, J. (2007). What supercomputers say: A study of five system logs. DSN 2007.
- Zhu, J., He, S., He, P., Liu, J., & Lyu, M. R. (2023). Loghub: A large collection of system log datasets for AI-driven log analytics. ISSRE 2023.
