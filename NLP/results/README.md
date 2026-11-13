# Results: full BGL, 4-core CPU run

Artifacts of the run described in `docs/report.md`, produced with `configs/bgl_cpu.yaml` on the
full Loghub BGL log (4,747,963 messages) and copied here by `make results OUT=outputs/bgl_cpu`.

| Path | Contents |
|---|---|
| `comparison.md`, `comparison.csv` | test-window precision, recall, F1, false positives and inference time per method |
| `logbert.json`, `logbert_full.json` | Approach A metrics and the (g, r) chosen on the validation period |
| `logbert/model.pt` | the trained LogBERT checkpoint (weights, vocabulary, hypersphere centre); load with `logad.logbert.trainer.load_checkpoint` |
| `logbert/history.json` | per-epoch training and dev losses |
| `logbert/*_tuning_grid.csv` | validation F1 for every (g, r) pair |
| `analysis/` | error analysis: template purity, per-category recall, agreement, missed alerts |

Approach B (the rule-guided LLM with Qwen2.5-7B-Instruct) was still running when these files were
first committed; its rules, decisions and metrics are added here when it finishes.
