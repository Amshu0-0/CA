# reference/: files of the first version of the NLP project

The NLP project (`../NLP`) is about **LogBERT** only. Its first version also compared LogBERT with a rule-guided language model
(Qwen2.5-7B-Instruct) and with plain keyword rules. That part was removed from the NLP project, but the cloud pipeline still runs those
two detectors next to LogBERT as **reference detectors** (the replay shows what a LogBERT window flag looks like beside them), and the
cluster evidence in `../evidence/` was recorded with them. So the three files they need are kept here, unchanged:

| File | What it is | Who reads it |
|---|---|---|
| `rules.json` | the 99 verified keyword rules of that experiment, ranked; the detector uses the top 30 | `scripts/export-nlp-bundle.py` copies it into `bundle/` |
| `llm_rule_based_decisions.csv` | the answers that the language model gave for each Drain template (saved; **no model runs in the cluster**, a decision is only looked up) | `scripts/export-nlp-bundle.py` copies it into `bundle/` |
| `comparison.csv` | the scores of all six methods of that experiment on the NLP project's whole test period (5,742 windows) | `scripts/tables.py`, for the "for comparison" lines under the result tables |

The code that applies the rules is `../processor/baselines.py` (about 50 lines). LogBERT itself does not use any of this: the trained
model and the Drain parser come from `../NLP` (`make export-bundle`).
