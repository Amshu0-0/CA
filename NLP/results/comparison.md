Test set: 5,742 windows, 495 anomalous (8.6%).

| Method | Precision | Recall | F1 | FP | FN | Msg-level F1 | Inference (s) |
|---|---:|---:|---:|---:|---:|---:|---:|
| A  LogBERT | 0.174 | 0.921 | 0.293 | 2,159 | 39 | - | 78.0 |
| A+ LogBERT, full-coverage masking (ext.) | 0.184 | 0.897 | 0.305 | 1,975 | 51 | - | 155.8 |
| B1 LLM zero-shot | 0.108 | 1.000 | 0.194 | 4,103 | 0 | 0.180 | 2813.0 |
| B2 LLM few-shot (case-based) | 0.323 | 0.941 | 0.481 | 976 | 29 | 0.444 | 3205.8 |
| B3 LLM rule-based | 0.489 | 0.943 | 0.644 | 488 | 28 | 0.491 | 3395.0 |
| B4 Keyword rules (no LLM) | 1.000 | 0.915 | 0.956 | 0 | 42 | 0.984 | 0.0 |
