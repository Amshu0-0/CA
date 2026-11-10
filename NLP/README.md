# Comparing BERT-Based and Rule-Guided Language Models for System Log Anomaly Detection

CSCI 390 — Natural Language Processing and Applications · Instructor: Dr. Qi Li
Project type: NLP research reproduction and extension (closest to Option 1, Text Classification, applied to system logs)

> **Status:** work in progress. The pipeline is being built stage by stage; see the commit history.

## Research question

How does a BERT-style model that learns patterns in **sequences of log-template IDs** compare with a
**rule-guided large language model that reads the log text itself** in identifying anomalous system events?

## Approaches

| | Approach A: LogBERT (reproduction) | Approach B: rule-guided LLM (partial LogRules adaptation) |
|---|---|---|
| Paper | Guo et al., 2021 — *LogBERT: Log Anomaly Detection via BERT* | Huang et al., 2025 — *LogRules* (Findings of NAACL 2025) |
| Input | Sequence of Drain template IDs in a 5-minute window | Text of each unique log template |
| Training | Self-supervised, **normal windows only** (masked log-key prediction + hypersphere loss) | Rules induced by an LLM from **labeled training examples**, verified on the training set |
| Decision | Window is anomalous if more than *r* masked keys fall outside the top-*g* predictions | Template label from the LLM (zero-shot / few-shot / rule-based) or from keyword matching |

Both approaches are evaluated on the **same 5-minute test windows** of the BGL dataset, using a
chronological 60/20/20 train/validation/test split.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Run the tests:

```bash
pytest
```
