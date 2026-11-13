Test set: 5,742 windows, 495 anomalous (8.6%).

| Method | Precision | Recall | F1 | FP | FN | Msg-level F1 | Inference (s) |
|---|---:|---:|---:|---:|---:|---:|---:|
| A  LogBERT | 0.174 | 0.921 | 0.293 | 2,159 | 39 | - | 78.0 |
| A+ LogBERT, full-coverage masking (ext.) | 0.184 | 0.897 | 0.305 | 1,975 | 51 | - | 155.8 |
