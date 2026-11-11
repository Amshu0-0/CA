"""The LogBERT network: a small Transformer encoder over log keys.

Architecture (following the LogBERT repository):

    token embedding + sinusoidal position encoding
      -> N pre-norm Transformer encoder layers (multi-head self-attention, GELU FFN)
      -> final LayerNorm
      -> two outputs:
           * MLKP head: a linear layer that scores every vocabulary key at
             every position (used to predict masked keys)
           * DIST representation: the hidden state of the first (<dist>)
             token, pulled towards the hypersphere centre by the VHM loss
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import torch
from torch import nn

from logad.logbert.vocab import PAD_INDEX


# Everything the model returns for one batch.
@dataclass
class LogBERTOutput:
    logits: torch.Tensor  # (batch, seq_len, vocab_size) scores for each key
    dist: torch.Tensor  # (batch, hidden) representation of each sequence


# Fixed sinusoidal position encodings from "Attention Is All You Need":
# PE[pos, 2i] = sin(pos / 10000^(2i/d)), PE[pos, 2i+1] = cos(...).
# Returns a (max_len, hidden) tensor.
def sinusoidal_positions(max_len: int, hidden: int) -> torch.Tensor:
    position = torch.arange(max_len, dtype=torch.float32).unsqueeze(1)
    frequency = torch.exp(
        torch.arange(0, hidden, 2, dtype=torch.float32) * (-math.log(10000.0) / hidden)
    )
    encoding = torch.zeros(max_len, hidden)
    encoding[:, 0::2] = torch.sin(position * frequency)
    encoding[:, 1::2] = torch.cos(position * frequency[: hidden // 2])
    return encoding


# Transformer encoder trained from scratch on log-key sequences.
class LogBERT(nn.Module):
    # vocab_size: number of tokens incl. specials; hidden: embedding size;
    # layers / heads: encoder depth and attention heads; ff_dim: width of the
    # feed-forward sublayer; max_len: longest sequence (incl. <dist>).
    def __init__(
        self,
        vocab_size: int,
        hidden: int = 256,
        layers: int = 2,
        heads: int = 4,
        ff_dim: int = 1024,
        dropout: float = 0.1,
        max_len: int = 512,
    ) -> None:
        super().__init__()
        if hidden % heads != 0:
            raise ValueError(f"hidden ({hidden}) must be divisible by heads ({heads})")
        self.max_len = max_len

        self.token_embedding = nn.Embedding(vocab_size, hidden, padding_idx=PAD_INDEX)
        # A buffer moves with .to(device) but is not a trainable parameter.
        self.register_buffer(
            "position_encoding", sinusoidal_positions(max_len, hidden), persistent=False
        )
        self.embedding_dropout = nn.Dropout(dropout)

        layer = nn.TransformerEncoderLayer(
            d_model=hidden,
            nhead=heads,
            dim_feedforward=ff_dim,
            dropout=dropout,
            activation="gelu",
            batch_first=True,
            norm_first=True,  # pre-norm, as in LogBERT's SublayerConnection
        )
        # Nested tensors are a fast path that does not support norm_first.
        self.encoder = nn.TransformerEncoder(layer, num_layers=layers, enable_nested_tensor=False)
        self.final_norm = nn.LayerNorm(hidden)
        self.mlkp_head = nn.Linear(hidden, vocab_size)

    # Encode a batch of token sequences.
    #
    # input_ids:      (batch, seq_len) token indices, <dist> first
    # attention_mask: (batch, seq_len) True for real tokens, False for padding;
    #                 derived from <pad> positions when not given
    def forward(
        self, input_ids: torch.Tensor, attention_mask: torch.Tensor | None = None
    ) -> LogBERTOutput:
        seq_len = input_ids.size(1)
        if seq_len > self.max_len:
            raise ValueError(f"Sequence length {seq_len} exceeds max_len {self.max_len}")
        if attention_mask is None:
            attention_mask = input_ids != PAD_INDEX

        positions = self.position_encoding[:seq_len].unsqueeze(0)  # (1, seq_len, hidden)
        hidden = self.embedding_dropout(self.token_embedding(input_ids) + positions)
        # PyTorch expects True where attention must be *blocked* (padding).
        hidden = self.encoder(hidden, src_key_padding_mask=~attention_mask)
        hidden = self.final_norm(hidden)

        return LogBERTOutput(logits=self.mlkp_head(hidden), dist=hidden[:, 0])
