"""Vocabulary that maps Drain template IDs (log keys) to model token indices.

Indices 0-3 are reserved for special tokens:

    0 <pad>   padding inside a batch
    1 <unk>   a log key never seen in normal training data
    2 <dist>  first token of every sequence; its output is the sequence
              representation used by the hypersphere loss (BERT's [CLS])
    3 <mask>  replaces the keys the model has to predict

The vocabulary is built only from keys in **normal training windows**, as in
LogBERT. A key that first appears at test time maps to <unk>; the model never
predicts <unk>, so an unseen key always counts as a miss, i.e. as anomalous.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import numpy as np

PAD_INDEX = 0
UNK_INDEX = 1
DIST_INDEX = 2
MASK_INDEX = 3
SPECIAL_TOKENS: tuple[str, ...] = ("<pad>", "<unk>", "<dist>", "<mask>")
NUM_SPECIAL = len(SPECIAL_TOKENS)


# Bidirectional mapping between template IDs and token indices.
class LogKeyVocab:
    # Build the vocabulary from an iterable of template IDs (duplicates are
    # fine). Keys are sorted so the mapping is deterministic.
    def __init__(self, template_ids: Iterable[int]) -> None:
        self.keys: list[int] = sorted({int(k) for k in template_ids})
        # Template ID -> token index; real keys start after the special tokens.
        self.key_to_index: dict[int, int] = {
            key: NUM_SPECIAL + i for i, key in enumerate(self.keys)
        }
        # Dense lookup table for fast vectorised encoding of whole windows:
        # lookup[template_id] = token index (UNK for unknown IDs).
        size = (max(self.keys) + 1) if self.keys else 1
        self._lookup = np.full(size, UNK_INDEX, dtype=np.int64)
        for key, index in self.key_to_index.items():
            self._lookup[key] = index

    # Total number of tokens, including the four special tokens.
    def __len__(self) -> int:
        return NUM_SPECIAL + len(self.keys)

    # Map an array of template IDs to token indices; unknown IDs -> <unk>.
    def encode(self, template_ids: np.ndarray | Iterable[int]) -> np.ndarray:
        ids = np.asarray(template_ids, dtype=np.int64)
        known = (ids >= 0) & (ids < len(self._lookup))
        # Clip before indexing so out-of-range IDs never index out of bounds;
        # they are then overwritten with <unk> by np.where.
        safe = np.clip(ids, 0, len(self._lookup) - 1)
        return np.where(known, self._lookup[safe], UNK_INDEX)

    # Map one token index back to its template ID, or to the special token's
    # name (e.g. "<unk>") for indices 0-3.
    def decode(self, index: int) -> int | str:
        if index < NUM_SPECIAL:
            return SPECIAL_TOKENS[index]
        return self.keys[index - NUM_SPECIAL]

    # Serialise to plain Python types (stored inside model checkpoints).
    def to_dict(self) -> dict[str, Any]:
        return {"keys": list(self.keys)}

    # Rebuild a vocabulary saved with `to_dict`.
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LogKeyVocab:
        return cls(data["keys"])
