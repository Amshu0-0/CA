Test set: 5,742 windows, 495 anomalous (8.6%).

| Method | Precision | Recall | F1 | FP | FN | Inference (s) |
|---|---:|---:|---:|---:|---:|---:|
| A  LogBERT | 0.175 | 0.921 | 0.294 | 2,149 | 39 | 4.9 |
| A+ LogBERT, full-coverage masking (ext.) | 0.162 | 0.952 | 0.276 | 2,445 | 24 | 10.1 |
