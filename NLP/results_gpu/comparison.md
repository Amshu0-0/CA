Test set: 5,742 windows, 495 anomalous (8.6%).

| Method | Precision | Recall | F1 | FP | FN | Msg-level F1 | Inference (s) |
|---|---:|---:|---:|---:|---:|---:|---:|
| A  LogBERT | 0.175 | 0.921 | 0.294 | 2,149 | 39 | - | 4.9 |
| A+ LogBERT, full-coverage masking (ext.) | 0.162 | 0.952 | 0.276 | 2,445 | 24 | - | 10.1 |
| B1 LLM zero-shot | 0.105 | 1.000 | 0.190 | 4,219 | 0 | 0.283 | 918.3 |
| B2 LLM few-shot (case-based) | 0.439 | 0.939 | 0.598 | 595 | 30 | 0.512 | 968.3 |
| B3 LLM rule-based | 0.416 | 0.992 | 0.586 | 690 | 4 | 0.504 | 1561.8 |
| B4 Keyword rules (no LLM) | 0.889 | 0.954 | 0.920 | 59 | 23 | 0.708 | 0.0 |
