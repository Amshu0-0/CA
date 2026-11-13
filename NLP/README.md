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

- Full GPU run on Colab: [`notebooks/colab_full_run.ipynb`](notebooks/colab_full_run.ipynb)

## Results

Run the full pipeline (below, or the Colab notebook), then `make results` to copy the tables here.
`results/comparison.md` will hold the main table: precision, recall, F1, false positives,
message-level F1 and inference time for each method on the shared test windows.

| Code | Method |
|---|---|
| A | LogBERT, original random masking |
| A+ | LogBERT with full-coverage masking (our extension: every key is checked once) |
| B1 / B2 / B3 | LLM zero-shot / few-shot (case-based) / rule-based |
| B4 | the same induced rules as plain keyword matching (no LLM) |

## Repository layout

```
configs/            experiment settings (bgl.yaml = full BGL, bgl_2k.yaml = smoke test)
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
notebooks/          Colab notebook for the full GPU run
tests/              141 unit and integration tests (offline; stub LLMs only)
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
number of `--set section.key=value` overrides. A GPU is strongly recommended; the Colab notebook does
everything on a free T4.

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
(rule induction, GPT-4o-mini as in LogRules) and the **classifier** (Qwen2.5-7B-Instruct). Any
OpenAI-compatible server works for either model. Three ways to run them:

1. **OpenAI + Ollama (default config).** Generator via the OpenAI API, classifier served locally:
   ```bash
   export OPENAI_API_KEY=...
   ollama pull qwen2.5:7b-instruct
   ```
   Then keep `ollama serve` running in another terminal.
2. **Colab GPU.** Run Qwen in-process with `transformers` (see the notebook):
   ```bash
   python scripts/run_rule_guided.py \
       --set llm.classifier.backend=transformers \
       --set llm.classifier.model=Qwen/Qwen2.5-7B-Instruct \
       --set llm.classifier.load_in_4bit=true
   ```
   This needs `pip install bitsandbytes`.
3. **No OpenAI key.** Use the same `--set` overrides on `llm.generator` so Qwen also induces the rules.

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
the cache is a complete record of all LLM calls.

## Live demo

```bash
python app/demo.py            # http://127.0.0.1:7860
python app/demo.py --share    # temporary public link for the class
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
| Trained models | `results/logbert/model.pt` after `make results` |
| Induced rules and all prompts | `results/rules/rules.md`, `prompts/` |
| Run instructions | this README, `notebooks/colab_full_run.ipynb` |
| Interactive demo | `app/demo.py` |

## References

- Guo, H., Yuan, S., & Wu, X. (2021). LogBERT: Log anomaly detection via BERT. arXiv:2103.04475.
- He, P., Zhu, J., Zheng, Z., & Lyu, M. R. (2017). Drain: An online log parsing approach with fixed depth tree. ICWS 2017.
- Huang, X., Zhang, T., & Zhao, W. (2025). LogRules: Enhancing log analysis capability of large language models through rules. Findings of NAACL 2025.
- Le, V.-H., & Zhang, H. (2022). Log-based anomaly detection with deep learning: How far are we? ICSE 2022.
- Oliner, A., & Stearley, J. (2007). What supercomputers say: A study of five system logs. DSN 2007.
- Zhu, J., He, S., He, P., Liu, J., & Lyu, M. R. (2023). Loghub: A large collection of system log datasets for AI-driven log analytics. ISSRE 2023.
