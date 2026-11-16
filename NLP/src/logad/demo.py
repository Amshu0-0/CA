"""Back end of the interactive demo (kept free of UI code so it can be tested).

``DemoEngine`` loads what the pipeline produced - the processed dataset, the
fitted Drain parser and the trained LogBERT with its tuned (g, r) - and answers
two questions:

* ``analyze_line``    "Is this log line anomalous?" The line is inserted into
                       a typical normal test window, because LogBERT judges
                       sequences, not single lines.
* ``analyze_window``  "Is this window anomalous?", optionally with a line
                       inserted at a chosen position.

For every message of the window the demo shows the rank LogBERT gave to its
true key, and whether that rank was a miss (outside the top-g guesses). If the
trained model is missing, the verdict is marked unavailable instead of failing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from logad.config import ExperimentConfig
from logad.data.bgl import parse_bgl_line
from logad.data.drain import TemplateExtractor
from logad.data.processed import DRAIN_FILE, load_processed
from logad.evaluation.predictions import load_method_results
from logad.logbert.dataset import window_to_chunks
from logad.logbert.detector import LogBERTDetector
from logad.logbert.trainer import load_checkpoint, resolve_device
from logad.utils import get_logger

logger = get_logger(__name__)

# Template ID used for a line that matches no known template.
UNSEEN = -1


# A user's input after parsing: what the model will see, and what was hidden.
@dataclass
class ParsedInput:
    content: str  # message text given to the model
    removed_tag: str | None  # alert tag stripped from a full BGL line (ground truth)
    template_id: int  # Drain template, or UNSEEN
    template: str  # template text (or the masked line if unseen)


# LogBERT's verdict, ready to display.
@dataclass
class Verdict:
    name: str
    available: bool  # False if the trained model is missing
    anomalous: bool | None = None
    summary: str = ""


# Everything the UI shows for one request.
@dataclass
class WindowAnalysis:
    parsed: ParsedInput | None
    verdict: Verdict
    messages: pd.DataFrame = field(default_factory=pd.DataFrame)  # one row per message


# Loads all artifacts once and answers demo requests.
class DemoEngine:
    def __init__(self, config: ExperimentConfig) -> None:
        self.config = config
        output_dir = Path(config.paths.output_dir)
        processed_dir = Path(config.paths.processed_dir)

        self.logs = load_processed(
            processed_dir, message_columns=["template_id", "label", "split", "content", "alert_tag"]
        )
        self.extractor = TemplateExtractor.load(processed_dir / DRAIN_FILE)
        self._load_logbert(output_dir)

    # ------------------------------------------------------------- loading

    # Trained LogBERT plus the (g, r) tuned on validation. Prefers the
    # full-coverage variant: every message is checked exactly once, which
    # makes the demo's per-message explanation complete and deterministic.
    def _load_logbert(self, output_dir: Path) -> None:
        self.detector: LogBERTDetector | None = None
        self.logbert_name = "LogBERT"
        checkpoint = output_dir / "logbert" / "model.pt"
        results = load_method_results(output_dir)
        method = next((m for m in ("logbert_full", "logbert") if m in results), None)
        if not checkpoint.exists() or method is None:
            logger.warning("LogBERT checkpoint or tuned thresholds missing; no verdicts")
            return
        settings = results[method]["settings"]
        self.g, self.r = int(settings["g"]), int(settings["r"])
        model, vocab, _, _ = load_checkpoint(checkpoint, resolve_device(self.config.logbert.device))
        self.detector = LogBERTDetector(
            model,
            vocab,
            device=next(model.parameters()).device,
            mask_ratio=settings["mask_ratio"],
            full_coverage=settings["full_coverage"],
            seed=self.config.logbert.detection.seed,
        )
        self.logbert_name = "LogBERT (full coverage)" if settings["full_coverage"] else "LogBERT"

    # ------------------------------------------------------------- inputs

    # Turn pasted text into model input. A full BGL line has its alert tag
    # removed (and remembered as ground truth); anything else is treated as
    # the message content itself. The content is mapped to a Drain template.
    def parse_input(self, text: str) -> ParsedInput:
        text = text.strip()
        record = parse_bgl_line(text)
        content, removed_tag = (record.content, record.alert_tag) if record else (text, None)
        template_id, template = self.extractor.match(content)
        return ParsedInput(
            content=content,
            removed_tag=removed_tag,
            template_id=UNSEEN if template_id is None else template_id,
            template=template,
        )

    # Normal test windows of a size that is easy to read in the demo
    # (falls back to the largest normal windows on small datasets).
    def candidate_windows(self, limit: int = 30) -> pd.DataFrame:
        windows = self.logs.windows_in("test")
        normal = windows[windows["label"] == 0]
        readable = normal[(normal["n_messages"] >= 10) & (normal["n_messages"] <= 120)]
        if readable.empty:
            readable = normal.nlargest(limit, "n_messages")
        step = max(1, len(readable) // limit)
        return readable.iloc[::step].head(limit).reset_index(drop=True)

    # ------------------------------------------------------------ analysis

    # Analyse a test window (by window_id), optionally inserting `line` before
    # message `position` (default: the middle). Returns LogBERT's verdict and a
    # per-message table.
    def analyze_window(
        self, window_id: int, line: str | None = None, position: int | None = None
    ) -> WindowAnalysis:
        window = self.logs.windows.set_index("window_id").loc[window_id]
        start, end = int(window["start_idx"]), int(window["end_idx"])
        messages = self.logs.messages.iloc[start:end]
        contents = messages["content"].astype(str).tolist()
        template_ids = self.logs.template_ids[start:end].tolist()
        truth = messages["alert_tag"].astype(str).tolist()
        texts = [self.logs.template_text[t] for t in template_ids]

        parsed, inserted = None, None
        if line and line.strip():
            parsed = self.parse_input(line)
            inserted = (
                len(contents) // 2 if position is None else max(0, min(position, len(contents)))
            )
            contents.insert(inserted, parsed.content)
            template_ids.insert(inserted, parsed.template_id)
            truth.insert(inserted, parsed.removed_tag or "?")
            texts.insert(inserted, parsed.template)

        # Row labels are strings so the inserted row can be marked in place.
        row_labels = [str(i) for i in range(len(contents))]
        if inserted is not None:
            row_labels[inserted] = f"{inserted} (your line)"
        table = pd.DataFrame(
            {"#": row_labels, "message": contents, "template": texts, "ground truth": truth}
        )

        verdict = self._logbert_verdict(np.array(template_ids), table, inserted)
        # Verdict column first, long free text last, so the table reads well.
        order = ["#", "ground truth", "LogBERT", "template", "message"]
        table = table[[column for column in order if column in table.columns]]
        return WindowAnalysis(parsed=parsed, verdict=verdict, messages=table)

    # Analyse a single line by inserting it into a reference normal window
    # (the first candidate window). Without any normal test window, only the
    # line itself could be judged, which LogBERT cannot do.
    def analyze_line(self, line: str) -> WindowAnalysis:
        reference = self.candidate_windows(limit=1)
        if not reference.empty:
            return self.analyze_window(int(reference.loc[0, "window_id"]), line)

        parsed = self.parse_input(line)
        table = pd.DataFrame({"#": ["0 (your line)"], "message": [parsed.content]})
        missing = Verdict("LogBERT", False, summary="no normal test window to insert into")
        return WindowAnalysis(parsed, missing, table)

    # Example inputs for the UI: the content of a few real alert messages
    # from the test period (one per alert template) and a few normal ones.
    def example_lines(self, n_alerts: int = 4, n_normal: int = 2) -> list[str]:
        test = self.logs.messages[self.logs.messages["split"].astype(str) == "test"]
        alerts = test[test["label"] == 1].drop_duplicates("template_id").head(n_alerts)
        normal = test[test["label"] == 0].drop_duplicates("template_id").head(n_normal)
        return alerts["content"].astype(str).tolist() + normal["content"].astype(str).tolist()

    # LogBERT on the (possibly modified) window. Adds a "LogBERT" column with
    # the rank of each masked message's true key; "MISSED" if outside top-g.
    def _logbert_verdict(
        self, template_ids: np.ndarray, table: pd.DataFrame, inserted: int | None
    ) -> Verdict:
        if self.detector is None:
            table["LogBERT"] = ""
            return Verdict("LogBERT", False, summary="train and evaluate LogBERT first")

        worst, column = self._logbert_misses(template_ids)
        table["LogBERT"] = column

        note = ""
        if inserted is not None:
            # Re-score the untouched window so the effect of the line is explicit.
            baseline, _ = self._logbert_misses(np.delete(template_ids, inserted))
            note = (
                f" Your line: {column[inserted]}. Without your line: {baseline} missed "
                f"({'already flagged' if baseline > self.r else 'normal'})."
            )
        return Verdict(
            self.logbert_name,
            True,
            worst > self.r,
            f"{worst} masked key(s) outside the top-{self.g} predictions "
            f"(flagged if more than r = {self.r}).{note}",
        )

    # Run LogBERT on one window's template IDs. Returns the largest missed-key
    # count over its chunks and one status string per message
    # ("ok (rank k)", "MISSED (rank k)" or "not masked").
    def _logbert_misses(self, template_ids: np.ndarray) -> tuple[int, list[str]]:
        assert self.detector is not None
        body = self.detector.model.max_len - 1
        # Unseen lines carry template ID -1, which the vocabulary maps to <unk>.
        encoded = self.detector.vocab.encode(template_ids)
        predictions = self.detector.predict_sequences(
            window_to_chunks(encoded, self.detector.model.max_len)
        )
        column = ["not masked"] * len(template_ids)
        worst = 0
        for c, prediction in enumerate(predictions):
            worst = max(worst, prediction.n_missed(self.g))
            for position, rank in zip(prediction.positions, prediction.ranks, strict=True):
                index = c * body + int(position) - 1
                column[index] = f"MISSED (rank {rank})" if rank >= self.g else f"ok (rank {rank})"
        return worst, column
