"""Log template extraction with Drain (He et al., 2017).

Drain groups log messages that were printed by the same statement in the
source code and replaces their variable parts with ``<*>``::

    "63543 double-hummer alignment exceptions"   ->  "<*> double-hummer alignment exceptions"
    "generating core.2275"                       ->  "generating core.<*>"

Each distinct template gets an integer *template ID* (LogBERT calls it a *log
key*). LogBERT reads sequences of these IDs; the rule-guided LLM reads the
template text.

We use the maintained ``drain3`` implementation, configured to match the
settings of the LogBERT repository for BGL (depth 3, similarity threshold 0.3,
and its masking regexes for hex numbers, IP addresses and integers).
"""

from __future__ import annotations

import pickle
from collections.abc import Iterable, Sequence
from pathlib import Path

import numpy as np
import pandas as pd
from drain3 import TemplateMiner
from drain3.masking import MaskingInstruction
from drain3.template_miner_config import TemplateMinerConfig
from tqdm import tqdm

from logad.utils import ensure_dir, get_logger

logger = get_logger(__name__)

# Text Drain puts in place of a variable token.
WILDCARD = "<*>"

# Masking regexes from LogBERT's BGL preprocessing, applied in order before
# Drain sees a message. (LogBERT's IP pattern has unescaped dots; they are
# escaped here so the pattern only matches real dotted quads.)
DEFAULT_MASKING: tuple[str, ...] = (
    r"(0x)[0-9a-fA-F]+",  # hexadecimal numbers, e.g. 0x00544eb8
    r"\d+\.\d+\.\d+\.\d+",  # IPv4 addresses
    r"\d+",  # any remaining integer
)


# Wraps a drain3 TemplateMiner and assigns stable, compact template IDs.
#
# Workflow:
#   extractor = TemplateExtractor()
#   ids = extractor.fit_transform(messages["content"])   # one ID per message
#   extractor.templates_frame()                           # ID -> template text
#   extractor.match("some new message")                   # used by the demo
class TemplateExtractor:
    # Configure Drain. `depth` and `similarity_threshold` are Drain's tree
    # depth and similarity threshold; `masking` is a list of regexes whose
    # matches are replaced by <*> before parsing.
    def __init__(
        self,
        depth: int = 3,
        similarity_threshold: float = 0.3,
        max_children: int = 100,
        masking: Sequence[str] = DEFAULT_MASKING,
    ) -> None:
        config = TemplateMinerConfig()
        config.drain_depth = depth
        config.drain_sim_th = similarity_threshold
        config.drain_max_children = max_children
        config.profiling_enabled = False
        # drain3 renders a mask as prefix + mask_with + suffix -> "<*>".
        config.mask_prefix = "<"
        config.mask_suffix = ">"
        config.masking_instructions = [MaskingInstruction(pattern, "*") for pattern in masking]
        # Passing a config object stops drain3 from looking for a drain3.ini file.
        self.miner = TemplateMiner(persistence_handler=None, config=config)

        # Filled in by fit_transform():
        # drain3 cluster ID -> our template ID
        self.cluster_to_template: dict[int, int] = {}
        # our template ID -> template text
        self.templates: dict[int, str] = {}

    # Parse every message (in the given, chronological order) and return an
    # int64 array with one template ID per message.
    #
    # Drain is an online algorithm: a cluster's template keeps generalising as
    # more messages arrive. We therefore assign IDs only after all messages
    # have been seen. Two clusters that end up with the *same* final template
    # text share one ID - this mirrors LogBERT's logparser, which derives the
    # event ID from a hash of the final template string.
    def fit_transform(self, contents: Iterable[str], show_progress: bool = True) -> np.ndarray:
        cluster_ids: list[int] = []
        for content in tqdm(
            contents, desc="Drain", unit="msg", disable=not show_progress, mininterval=2.0
        ):
            result = self.miner.add_log_message(content)
            cluster_ids.append(result["cluster_id"])

        # Final template text of every cluster.
        cluster_template = {
            cluster.cluster_id: cluster.get_template() for cluster in self.miner.drain.clusters
        }

        # Number templates 1, 2, 3, ... in order of first appearance so that IDs
        # are deterministic and roughly chronological.
        text_to_id: dict[str, int] = {}
        self.cluster_to_template = {}
        for cluster_id in cluster_ids:
            if cluster_id in self.cluster_to_template:
                continue
            text = cluster_template[cluster_id]
            if text not in text_to_id:
                text_to_id[text] = len(text_to_id) + 1
            self.cluster_to_template[cluster_id] = text_to_id[text]
        self.templates = {template_id: text for text, template_id in text_to_id.items()}

        logger.info(
            "Drain found %d clusters -> %d unique templates",
            len(cluster_template),
            len(self.templates),
        )
        lookup = self.cluster_to_template
        return np.fromiter((lookup[c] for c in cluster_ids), dtype=np.int64, count=len(cluster_ids))

    # Map a single, possibly unseen message to a known template without
    # changing the model. Returns (template_id, template_text), or
    # (None, masked_message) when no existing template matches exactly.
    def match(self, content: str) -> tuple[int | None, str]:
        cluster = self.miner.match(content, full_search_strategy="fallback")
        if cluster is None or cluster.cluster_id not in self.cluster_to_template:
            return None, self.mask(content)
        template_id = self.cluster_to_template[cluster.cluster_id]
        return template_id, self.templates[template_id]

    # Apply only the masking step (numbers, hex values, IPs -> <*>). Used to
    # show an unseen message in the same normalised form as the templates.
    def mask(self, content: str) -> str:
        return self.miner.masker.mask(content)

    # Templates as a DataFrame with columns [template_id, template].
    def templates_frame(self) -> pd.DataFrame:
        rows = sorted(self.templates.items())
        return pd.DataFrame(rows, columns=["template_id", "template"])

    # Pickle the fitted extractor (Drain tree + ID mappings) so the demo can
    # map new log lines onto the same template IDs used during training.
    def save(self, path: str | Path) -> None:
        path = Path(path)
        ensure_dir(path.parent)
        with path.open("wb") as fh:
            pickle.dump(self, fh)

    # Load an extractor saved with `save`. Only load files you created
    # yourself: unpickling runs code from the file.
    @staticmethod
    def load(path: str | Path) -> TemplateExtractor:
        with Path(path).open("rb") as fh:
            extractor = pickle.load(fh)
        if not isinstance(extractor, TemplateExtractor):
            raise TypeError(f"{path} does not contain a TemplateExtractor")
        return extractor
