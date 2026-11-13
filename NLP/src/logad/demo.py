"""Back end of the interactive demo (kept free of UI code so it can be tested).

``DemoEngine`` loads every artifact the pipeline produced - the processed
dataset, the fitted Drain parser, the trained LogBERT with its tuned (g, r),
the ranked rules and the LLM's saved decisions - and answers two questions
for both approaches side by side:

* ``analyze_line``    "Is this log line anomalous?" The line is inserted into
                       a typical normal test window, because LogBERT judges
                       sequences, not single lines.
* ``analyze_window``  "Is this window anomalous?", optionally with a line
                       inserted at a chosen position.

If an artifact is missing (e.g. no LLM was run) the corresponding verdict is
marked unavailable instead of failing, so the demo works at every stage.
"""

from __future__ import annotations

import socket
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import numpy as np
import pandas as pd

from logad.config import ExperimentConfig
from logad.data.bgl import parse_bgl_line
from logad.data.drain import TemplateExtractor
from logad.data.processed import DRAIN_FILE, load_processed
from logad.evaluation.predictions import load_method_results
from logad.evaluation.template_level import TemplateDecision
from logad.llm.classifier import LLMTemplateClassifier
from logad.llm.client import create_client
from logad.logbert.dataset import window_to_chunks
from logad.logbert.detector import LogBERTDetector
from logad.logbert.trainer import load_checkpoint, resolve_device
from logad.rules.keyword import KeywordClassifier
from logad.rules.rule import Rule, load_rules
from logad.utils import get_logger

logger = get_logger(__name__)

# Template ID used for a line that matches no known template.
UNSEEN = -1


# A user's input after parsing: what the models will see, and what was hidden.
@dataclass
class ParsedInput:
    content: str  # message text given to the models
    removed_tag: str | None  # alert tag stripped from a full BGL line (ground truth)
    template_id: int  # Drain template, or UNSEEN
    template: str  # template text (or the masked line if unseen)


# One method's verdict, ready to display.
@dataclass
class Verdict:
    method: str
    available: bool  # False if the method's artifacts are missing
    anomalous: bool | None = None
    summary: str = ""


# Everything the UI shows for one request.
@dataclass
class WindowAnalysis:
    parsed: ParsedInput | None
    verdicts: list[Verdict]
    messages: pd.DataFrame = field(default_factory=pd.DataFrame)  # one row per message


# True if a TCP connection to the server behind `base_url` can be opened.
# Lets the demo skip a local LLM server that is not running instead of
# waiting for request timeouts.
def server_reachable(base_url: str | None, timeout: float = 1.0) -> bool:
    if base_url is None:
        return True  # hosted API; reachability is checked on the first call
    parsed = urlparse(base_url)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((parsed.hostname, port), timeout=timeout):
            return True
    except OSError:
        return False


# Loads all artifacts once and answers demo requests.
class DemoEngine:
    def __init__(self, config: ExperimentConfig, enable_live_llm: bool = True) -> None:
        self.config = config
        output_dir = Path(config.paths.output_dir)
        processed_dir = Path(config.paths.processed_dir)

        self.logs = load_processed(
            processed_dir, message_columns=["template_id", "label", "split", "content", "alert_tag"]
        )
        self.extractor = TemplateExtractor.load(processed_dir / DRAIN_FILE)
        self._load_logbert(output_dir)
        self._load_rule_guided(output_dir, enable_live_llm)

    # ------------------------------------------------------------- loading

    # Trained LogBERT plus the (g, r) tuned on validation. Prefers the
    # full-coverage variant: every message is checked exactly once, which
    # makes the demo's per-message explanation complete and deterministic.
    def _load_logbert(self, output_dir: Path) -> None:
        self.detector: LogBERTDetector | None = None
        checkpoint = output_dir / "logbert" / "model.pt"
        results = load_method_results(output_dir)
        method = next((m for m in ("logbert_full", "logbert") if m in results), None)
        if not checkpoint.exists() or method is None:
            logger.warning("LogBERT checkpoint or tuned thresholds missing; A is unavailable")
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

    # Ranked rules (keyword baseline), the LLM's saved rule-based decisions,
    # and - if the server is reachable - a live LLM for unseen templates.
    def _load_rule_guided(self, output_dir: Path, enable_live_llm: bool) -> None:
        rules_path = output_dir / "rules" / "rules.json"
        self.rules: list[Rule] = (
            load_rules(rules_path)[: self.config.rules.top_k] if rules_path.exists() else []
        )
        self.keyword = KeywordClassifier(self.rules) if self.rules else None

        self.saved_llm: dict[int, TemplateDecision] = {}
        decisions_path = output_dir / "rule_guided" / "llm_rule_based_decisions.csv"
        if decisions_path.exists():
            table = pd.read_csv(decisions_path, keep_default_na=False)
            for row in table.itertuples():
                # Empty cells mean "no rule cited"; numbers may be saved as "3.0".
                cell = str(row.rule).strip()
                rule = int(float(cell)) if cell not in ("", "nan", "None") else None
                self.saved_llm[int(row.template_id)] = TemplateDecision(
                    int(row.template_id), int(row.label), rule=rule, reason=str(row.reason)
                )

        self.live_llm: LLMTemplateClassifier | None = None
        backend = self.config.llm.classifier
        # Only an OpenAI-compatible backend talks to a server that may be down;
        # a transformers backend loads the model in this process instead.
        reachable = backend.backend != "openai" or server_reachable(backend.base_url)
        if enable_live_llm and self.rules and not reachable:
            logger.warning("LLM server %s is not reachable; live LLM disabled", backend.base_url)
        if enable_live_llm and self.rules and reachable:
            try:
                client = create_client(backend, self.config.llm.cache_dir)
                self.live_llm = LLMTemplateClassifier(client, "rule_based", rules=self.rules)
            except Exception as exc:  # noqa: BLE001 - demo must start without an LLM
                logger.warning("Live LLM disabled: %s", exc)

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

    # ---------------------------------------------------------- approach B

    # Keyword-baseline decision for one template text.
    def _keyword_decision(self, template_id: int, text: str) -> TemplateDecision | None:
        return self.keyword.classify(template_id, text) if self.keyword else None

    # LLM rule-based decision: the decision saved by the experiment for known
    # templates, otherwise a live call if an LLM is reachable.
    def _llm_decision(self, template_id: int, text: str) -> tuple[TemplateDecision | None, str]:
        if template_id in self.saved_llm:
            return self.saved_llm[template_id], "saved experiment decision"
        if self.live_llm is not None:
            return self.live_llm.classify(template_id, text), "live LLM call"
        return None, "no LLM decision (template not in the experiment and no LLM server running)"

    # ------------------------------------------------------------ analysis

    # Analyse a test window (by window_id), optionally inserting `line` before
    # message `position` (default: the middle). Returns verdicts of LogBERT,
    # the keyword rules and the rule-based LLM, plus a per-message table.
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

        verdicts = [
            self._logbert_verdict(np.array(template_ids), table, inserted),
            *self._rule_guided_verdicts(template_ids, texts, table, inserted),
        ]
        # Verdict columns first, long free text last, so the table reads well.
        order = ["#", "ground truth", "LogBERT", "keyword", "LLM", "template", "message"]
        table = table[[column for column in order if column in table.columns]]
        return WindowAnalysis(parsed=parsed, verdicts=verdicts, messages=table)

    # Analyse a single line by inserting it into a reference normal window
    # (the first candidate window). Without any normal test window, only the
    # line itself can be judged, which LogBERT cannot do.
    def analyze_line(self, line: str) -> WindowAnalysis:
        reference = self.candidate_windows(limit=1)
        if not reference.empty:
            return self.analyze_window(int(reference.loc[0, "window_id"]), line)

        parsed = self.parse_input(line)
        table = pd.DataFrame({"#": ["0 (your line)"], "message": [parsed.content]})
        verdicts = self._rule_guided_verdicts([parsed.template_id], [parsed.template], table, 0)
        missing = Verdict("A: LogBERT", False, summary="no normal test window to insert into")
        return WindowAnalysis(parsed, [missing, *verdicts], table)

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
            return Verdict("A: LogBERT", False, summary="train and evaluate LogBERT first")

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
            f"A: {self.logbert_name}",
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

    # Keyword rules and the rule-based LLM: a window is anomalous if any of
    # its messages' templates is labeled abnormal. Adds a column per method.
    def _rule_guided_verdicts(
        self, template_ids: list[int], texts: list[str], table: pd.DataFrame, inserted: int | None
    ) -> list[Verdict]:
        verdicts = []

        if self.keyword is None:
            verdicts.append(
                Verdict("B4: keyword rules", False, summary="run induce_rules.py first")
            )
        else:
            decisions = [
                self._keyword_decision(t, x) for t, x in zip(template_ids, texts, strict=True)
            ]
            table["keyword"] = ["abnormal" if d.label else "normal" for d in decisions]
            verdicts.append(self._summarize_b("B4: keyword rules (no LLM)", decisions, inserted))

        llm = [self._llm_decision(t, x) for t, x in zip(template_ids, texts, strict=True)]
        if all(d is None for d, _ in llm):
            verdicts.append(Verdict("B3: LLM rule-based", False, summary=llm[0][1] if llm else ""))
        else:
            table["LLM"] = [
                "?" if d is None else ("abnormal" if d.label else "normal") for d, _ in llm
            ]
            decisions = [d for d, _ in llm]
            source = llm[inserted][1] if inserted is not None else "saved experiment decisions"
            verdicts.append(
                self._summarize_b(f"B3: LLM rule-based ({source})", decisions, inserted)
            )
        return verdicts

    # One Approach B verdict: flagged if any message is abnormal; explain the
    # first flagged message (preferring the inserted one) and its rule. With an
    # inserted line, also state the line's own label and what the window gets
    # without it, as LogBERT's verdict does: on the full test period the
    # context window may already contain (false) alarms of its own.
    def _summarize_b(
        self, name: str, decisions: list[TemplateDecision | None], inserted: int | None
    ) -> Verdict:
        flagged = [i for i, d in enumerate(decisions) if d is not None and d.label == 1]
        note = ""
        if inserted is not None:
            own = decisions[inserted]
            label = "no decision" if own is None else ("abnormal" if own.label else "normal")
            others = sum(1 for i in flagged if i != inserted)
            note = (
                f" Your line: {label}. Without your line: {others} flagged "
                f"({'already flagged' if others else 'normal'})."
            )
        if not flagged:
            return Verdict(name, True, False, f"no message matched an abnormal rule.{note}")
        focus = inserted if inserted in flagged else flagged[0]
        decision = decisions[focus]
        assert decision is not None
        rule = ""
        if decision.rule is not None and 1 <= decision.rule <= len(self.rules):
            rule = f' Rule #{decision.rule}: "{self.rules[decision.rule - 1].text}"'
        reason = f" ({decision.reason})" if decision.reason else ""
        return Verdict(
            name,
            True,
            True,
            f"{len(flagged)} message(s) flagged; message #{focus}{reason}.{rule}{note}",
        )
