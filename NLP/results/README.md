# Results: full BGL, 4-core CPU run

Artifacts of the run described in `docs/report.md`, produced with `configs/bgl_cpu.yaml` on the
full Loghub BGL log (4,747,963 messages) and copied here by `make results OUT=outputs/bgl_cpu`.
Approach B used Qwen2.5-7B-Instruct (official 4-bit GGUF, `q4_k_m`) served by llama-cpp-python
on the same 4 CPU cores, for both rule induction and classification.

## Test period, window level

5,742 windows, of which 495 (8.6%) are anomalous. Inference time: scoring the test windows (A) or
classifying the 472 test templates (B), on 4 CPU cores.

| Method | Precision | Recall | F1 | FP | FN | Msg-level F1 | Inference |
|---|---:|---:|---:|---:|---:|---:|---:|
| A: LogBERT | 0.174 | 0.921 | 0.293 | 2,159 | 39 | - | 78 s |
| A+: LogBERT, full-coverage masking | 0.184 | 0.897 | 0.305 | 1,975 | 51 | - | 156 s |
| B1: LLM zero-shot | 0.108 | 1.000 | 0.194 | 4,103 | 0 | 0.180 | 47 min |
| B2: LLM few-shot | 0.323 | 0.941 | 0.481 | 976 | 29 | 0.444 | 53 min |
| B3: LLM rule-based | 0.489 | 0.943 | 0.644 | 488 | 28 | 0.491 | 57 min |
| B4: Keyword rules (no LLM) | 1.000 | 0.915 | 0.956 | 0 | 42 | 0.984 | 8 ms |

One-off costs: LogBERT training took 60 minutes; rule induction took 50 minutes (42 LLM calls).

## Files

| Path | Contents |
|---|---|
| `comparison.md`, `comparison.csv` | the table above, recomputed from the saved predictions by `scripts/compare.py` |
| `logbert.json`, `logbert_full.json` | Approach A metrics and the (g, r) chosen on the validation period |
| `llm_zero_shot.json`, `llm_few_shot.json`, `llm_rule_based.json`, `keyword_rules.json` | Approach B metrics (window and message level), unparsed answers and timing per condition |
| `logbert/model.pt` | the trained LogBERT checkpoint (weights, vocabulary, hypersphere centre); load with `logad.logbert.trainer.load_checkpoint` |
| `logbert/history.json` | per-epoch training and dev losses |
| `logbert/*_tuning_grid.csv` | validation F1 for every (g, r) pair |
| `rules/rules.md`, `rules/rules.json` | the 99 verified rules in rank order; the top 30 are used |
| `rules/candidates.json` | all 160 rules the LLM proposed, before verification |
| `rules/induction_summary.json` | number of induction batches, candidates and kept rules |
| `rule_guided/<method>_decisions.csv` | one row per template: label, cited rule, the model's reason, and the template's counts per period |
| `analysis/` | error analysis: `analysis.md` plus one CSV per table (template purity, label drift, per-category recall, agreement, combinations, missed alerts, rule faithfulness, top-k sensitivity) |
| `llm_cache/openai_qwen2.5_7b-instruct.jsonl` | every prompt and raw answer of the run: 42 induction calls and 3 × 472 classifications |

## Replaying Approach B without a model

Every LLM request is answered from the response cache when an identical request was made before,
so the committed cache replays the whole of Approach B without a model server. This was checked
with the server stopped: the rules and every template decision came out identical. The preprocessed
data is needed first (`make data` and `make preprocess`).

```bash
mkdir -p outputs/llm_cache && cp results/llm_cache/*.jsonl outputs/llm_cache/
```

```bash
python scripts/induce_rules.py --config configs/bgl_cpu.yaml
```

```bash
python scripts/run_rule_guided.py --config configs/bgl_cpu.yaml
```

To save CPU time, `configs/bgl_cpu.yaml` classifies only the test templates. The keyword baseline
costs nothing, so it was also scored on the validation period:

```bash
python scripts/run_rule_guided.py --config configs/bgl_cpu.yaml --conditions keyword --set "deduction.splits=[val, test]"
```
