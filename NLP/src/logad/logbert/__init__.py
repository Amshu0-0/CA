"""Approach A: LogBERT (Guo, Yuan & Wu, 2021) reproduced in PyTorch.

LogBERT is *not* a pretrained language model that reads words. It is a small
Transformer encoder trained from scratch on sequences of log keys (Drain
template IDs) taken from normal windows only, with two self-supervised tasks:

* Masked Log Key Prediction (MLKP): hide some keys and predict them from context.
* Volume of Hypersphere Minimisation (VHM): pull the representation of each
  normal sequence (the output at the special DIST token) towards a common
  centre, so normal behaviour forms a compact cluster.

At test time a window is anomalous if more than ``r`` of its masked keys are
not among the model's top-``g`` predictions.

Modules: ``vocab`` (log-key vocabulary), ``dataset`` (windows -> masked
sequences), ``model`` (the encoder), ``trainer`` (training loop) and
``detector`` (scoring windows and tuning g / r).
"""
