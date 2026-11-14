# The NLP step of the pipeline: what happens to every log line, without Kafka, MongoDB or HTTP around it.
#
# Why this is a file of its own: processor.py only moves messages in and results out. The thinking is here, so the very same code runs
# in the cluster (processor.py) and on a laptop (scripts/local-replay.py), and so it can be tested without a cluster (tests/).
#
# What happens, in order:
#   1. A Kafka message holds one raw BGL log line. parse_bgl_line (from your NLP project, the package logad) splits off the alert tag.
#      The tag is the ground truth. It is stored for the evaluation only: no detector ever sees it, as in the NLP project.
#   2. Drain (drain.pkl, fitted by the NLP project) maps the message text to a template ID: the same IDs that LogBERT and the rules know.
#   3. Two detectors work on single templates: the keyword rules (condition B4) and the saved decisions of the rule-guided LLM (B3).
#   4. The line goes into a 5-minute window that slides by 1 minute: the same windows as in the NLP project's evaluation (class Windower).
#   5. When a window is complete (a line from after its end has arrived), LogBERT (A, the full-coverage variant) scores it, and the two
#      template detectors lift their labels to the window: a window is flagged if any of its messages is.
#   6. The results are documents for MongoDB: one per log line ("events"), one per window ("windows"), one per replay ("runs").
#
# Restarts: a window can be scored again after a crash and gives the same answer (the masks LogBERT uses are seeded per window), and the
# documents have fixed ids, so writing them twice changes nothing. The processor commits a Kafka offset only up to the oldest line that an
# open window still needs, and the position of the window grid is stored with every batch, so a restarted processor continues exactly where
# the old one stopped (tests/test_restart.py crashes the processor at every possible place and compares the result).
#
# It needs the NLP project's package (logad), torch, numpy, and the files that NLP-pipeline/scripts/export-nlp-bundle.py collects.

import bisect
import csv
import json
import os
import time
from collections import Counter, namedtuple

import numpy as np

from logad.data.bgl import parse_bgl_line
from logad.data.drain import TemplateExtractor
from logad.logbert.dataset import window_to_chunks
from logad.logbert.detector import LogBERTDetector
from logad.logbert.trainer import load_checkpoint
from logad.rules.keyword import KeywordClassifier
from logad.rules.rule import load_rules

# How many masked copies of a window LogBERT looks at in one forward pass. The library's default is 128, which is too much for one very
# busy window: a window of 40,000 lines needed about 2 GB (more than the processor may use), while 16 needs about 0.9 GB, gives the same
# answer (the masks do not depend on the batch) and is even a little faster on 2 CPUs. Measured with the real model; see processor.yaml.
SCORE_BATCH = 16

# The template ID of a line that matches no known template. The NLP demo uses the same value. LogBERT's vocabulary maps it to <unk>,
# which can never be predicted, so an unseen line always counts as a missed key.
UNSEEN = -1

# One message as it comes from Kafka.
Record = namedtuple("Record", "topic partition offset timestamp_ms value")

# What the detectors say about one line. keyword is 0 or 1; llm is 0, 1 or None (the saved experiment has no decision for this template).
Line = namedtuple("Line", "template_id template keyword keyword_rule keyword_reason llm")

# What LogBERT says about one window: flag 0 or 1, the largest number of masked keys outside the top-g (over the window's chunks), and
# how many chunks the window was cut into (a chunk holds at most max_len - 1 keys).
Score = namedtuple("Score", "flag missed chunks")

# One line inside a window: its Kafka offset, its time (seconds since 1970), its template, and the labels. alert is the ground truth.
Item = namedtuple("Item", "offset ts template_id alert keyword llm")


class BundleError(Exception):
    pass


# ---------------------------------------------------------------- the detectors

# Reads the saved decisions of the rule-guided LLM: {template id: (label, template text)}.
def read_llm_decisions(path):
    decisions = {}

    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            decisions[int(row["template_id"])] = (int(row["label"]), row.get("template", ""))

    return decisions


# The text of a template with every run of white space turned into one space (a CSV file and a pickle may differ in that, not in meaning).
def squash(text):
    return " ".join(str(text).split())


# Everything that was trained or saved by the NLP project, loaded once. classify() looks at one line, score() at one window.
class Detectors:
    def __init__(self, extractor, keyword, llm, logbert, g, r, size_seconds=300, step_seconds=60, cache_size=50000):
        self.extractor = extractor
        self.keyword = keyword
        self.llm = llm
        self.logbert = logbert
        self.g = g
        self.r = r
        self.size_seconds = size_seconds
        self.step_seconds = step_seconds
        self.cache_size = cache_size
        self.cache = {}

    # Load the bundle folder: drain.pkl, model.pt, rules.json, llm_rule_based_decisions.csv and settings.json.
    @classmethod
    def load(cls, directory):
        def path(name):
            full = os.path.join(directory, name)

            if not os.path.exists(full):
                raise BundleError(f"{full} is missing. Run  python3 NLP-pipeline/scripts/export-nlp-bundle.py  (README: Step 1)")

            return full

        with open(path("settings.json"), encoding="utf-8") as f:
            settings = json.load(f)

        extractor = TemplateExtractor.load(path("drain.pkl"))
        rules = load_rules(path("rules.json"))[: int(settings["keyword_top_k"])]
        llm = read_llm_decisions(path("llm_rule_based_decisions.csv"))
        model, vocab, _centre, _checkpoint = load_checkpoint(path("model.pt"), "cpu")
        setting = settings["logbert"]
        logbert = LogBERTDetector(model, vocab, device="cpu", mask_ratio=float(setting["mask_ratio"]),
                                  full_coverage=bool(setting["full_coverage"]), batch_size=SCORE_BATCH, seed=int(setting["seed"]))

        detectors = cls(extractor, KeywordClassifier(rules), llm, logbert, int(setting["g"]), int(setting["r"]),
                        int(settings["window"]["size_seconds"]), int(settings["window"]["step_seconds"]))
        detectors.check_bundle()
        return detectors

    # The files must belong together. drain.pkl gives the template IDs; model.pt and the saved decisions were made with IDs from a drain.pkl.
    # If you ran "make preprocess" again after training, the IDs may mean other templates, and every verdict would be wrong without any
    # error. So: every template of the saved decisions must have the same text in drain.pkl, and every key LogBERT knows must exist in it.
    def check_bundle(self):
        known = self.extractor.templates
        wrong = [(i, text) for i, (_label, text) in self.llm.items() if squash(known.get(i, "")) != squash(text)]
        missing = [k for k in self.logbert.vocab.keys if k not in known]

        if wrong or missing:
            sample = ", ".join(f"template {i}" for i, _ in wrong[:5])
            raise BundleError(f"drain.pkl does not belong to the model and the decisions: {len(wrong)} templates of llm_rule_based_decisions.csv "
                              f"differ in drain.pkl ({sample}) and {len(missing)} keys of the LogBERT vocabulary are not in it. "
                              "Make the bundle again from ONE run of the NLP project:  python3 NLP-pipeline/scripts/export-nlp-bundle.py")

    # The verdicts of one log line (its text without the alert tag). Drain's match() does not change the model, so the answer for a text
    # never changes, and repeated texts (most of a log) are answered from a small cache.
    def classify(self, content):
        line = self.cache.get(content)

        if line is None:
            template_id, text = self.extractor.match(content)
            template_id = UNSEEN if template_id is None else template_id
            decision = self.keyword.classify(template_id, text)
            saved = self.llm.get(template_id)
            line = Line(template_id, text, int(decision.label), decision.rule, decision.reason, None if saved is None else saved[0])

            if len(self.cache) >= self.cache_size:
                self.cache.clear()

            self.cache[content] = line

        return line

    # LogBERT on one window (the template IDs of its lines, in time order). The window is cut into chunks of at most max_len - 1 keys,
    # half of the keys of a chunk are masked in turn (full coverage), and the window is flagged if a chunk has more than r masked keys that
    # are not among the model's top-g guesses. This is what the NLP demo does for one window; the masks are seeded by the position of the
    # chunk inside the window, so the same window always gets the same answer, whatever else is scored at the same time.
    def score(self, template_ids):
        if len(template_ids) == 0:
            return Score(0, 0, 0)

        encoded = self.logbert.vocab.encode(np.asarray(template_ids, dtype=np.int64))
        chunks = window_to_chunks(encoded, self.logbert.model.max_len)
        missed = max(p.n_missed(self.g) for p in self.logbert.predict_sequences(chunks))

        return Score(int(missed > self.r), int(missed), len(chunks))


# ---------------------------------------------------------------- the windows

# A complete window: [start, end) in seconds, and the lines inside it in time order.
class Window:
    def __init__(self, start, end, items):
        self.start = start
        self.end = end
        self.items = items


# Sliding windows over a log, one line at a time. It gives the same windows as logad.data.windows.sliding_windows, which the NLP project
# evaluated on (tests/test_windower.py compares them on random logs):
#   - the first window starts at the time of the first line, and the start moves on by `step` seconds each time;
#   - a window covers [start, start + size);
#   - empty windows are dropped, and a window with exactly the same lines as the one before it is dropped;
#   - when the log ends, the windowing stops after the first window that reaches the last line (flush()).
# A window is complete as soon as a line arrives whose time is at or after the window's end. Lines that arrive a little out of order are
# put where they belong. A line older than the start of the oldest open window (late) is not needed by any window that is still open, so it
# is not added. A window that was already handed out is never changed afterwards: a line that arrives too late for it only goes into the
# windows that are still open. (A replay of a log that is sorted by time never has such lines; the export script sorts it.)
class Windower:
    def __init__(self, size, step, origin=None, next_k=0, last_key=None, done=False):
        if size <= 0 or step <= 0:
            raise ValueError("window size and step must be positive")

        self.size = size
        self.step = step

        # The grid: window k starts at origin + k * step. next_k is the first window that has not been handed out.
        self.origin = origin
        self.next_k = next_k
        self.last_key = tuple(last_key) if last_key else None
        self.done = done

        # The lines that an open window may still need, sorted by time (equal times keep their arrival order).
        self.items = []
        self.times = []
        self.newest = None

    # The start of the oldest open window: lines before it are not needed any more.
    @property
    def floor(self):
        return None if self.origin is None else self.origin + self.next_k * self.step

    # The offset of the oldest line that is still needed (the processor must not commit past it), or None.
    def oldest_offset(self):
        return min((i.offset for i in self.items), default=None)

    # Add one line. Returns (status, windows): status is "ok", "reordered" (older than the newest line, but still useful) or "late"
    # (not added); windows are the windows that this line completed, in order.
    def add(self, item):
        if self.done:
            return "late", []

        if self.origin is None:
            self.origin = item.ts

        if item.ts < self.floor:
            return "late", []

        status = "reordered" if self.newest is not None and item.ts < self.newest else "ok"
        position = bisect.bisect_right(self.times, item.ts)
        self.times.insert(position, item.ts)
        self.items.insert(position, item)

        if self.newest is None or item.ts > self.newest:
            self.newest = item.ts

        return status, self._complete(self.newest)

    # The window with this number: (start, end, first index, index after the last) inside the open lines.
    def _look(self, k):
        start = self.origin + k * self.step
        end = start + self.size
        return start, end, bisect.bisect_left(self.times, start), bisect.bisect_left(self.times, end)

    # Hand out the window as an object, or None if it is empty or holds the same lines as the one before.
    def _take(self, start, end, lo, hi):
        if hi <= lo:
            return None

        items = self.items[lo:hi]
        key = (len(items), items[0].offset, items[-1].offset, sum(i.offset for i in items))

        if key == self.last_key:
            return None

        self.last_key = key
        return Window(start, end, items)

    # Hand out every window that ends at or before `until`, then drop the lines that no open window needs.
    def _complete(self, until):
        windows = []

        while self.origin + self.next_k * self.step + self.size <= until:
            window = self._take(*self._look(self.next_k))
            self.next_k += 1

            if window:
                windows.append(window)

        cut = bisect.bisect_left(self.times, self.floor)
        del self.items[:cut]
        del self.times[:cut]
        return windows

    # The log is over: hand out the windows that are left, as the batch function does. It stops after the first window that reaches
    # the last line, because every later window would only repeat a shrinking end of the log.
    def flush(self):
        windows = []

        if not self.done and self.items:
            while self.origin + self.next_k * self.step <= self.times[-1]:
                start, end, lo, hi = self._look(self.next_k)
                window = self._take(start, end, lo, hi)
                self.next_k += 1

                if window:
                    windows.append(window)

                if hi >= len(self.items):
                    break

        self.items, self.times = [], []
        self.done = True
        return windows

    # What must be stored with every batch so that a restarted processor can carry on with the same grid.
    def state(self):
        return {"origin": self.origin, "next_k": self.next_k, "last_key": list(self.last_key) if self.last_key else None, "done": self.done}


# ---------------------------------------------------------------- the engine

# What one batch of messages produced. The processor writes events, then windows, then the run documents, and only then commits.
class Output:
    def __init__(self):
        self.events = []
        self.windows = []

        # run id -> {"set": fields to set, "insert": fields to set only when the run document is new}
        self.runs = {}

        # (level, message, fields) lines for the processor's log
        self.logs = []

        # numbers for the metrics: lines, alerts, unseen, bad, late, reordered, smoke, windows, flagged_keyword, flagged_llm, flagged_logbert
        self.counts = Counter()

        # seconds that LogBERT needed for each window of this batch
        self.seconds = []

    def run(self, run):
        return self.runs.setdefault(run, {"set": {}, "insert": {}})


# Turns Kafka messages into documents. One instance per processor; it keeps the open windows of the replay that is running.
#
# The messages are JSON objects with a "kind":
#   log    {"kind": "log", "run": ..., "seq": ..., "line": one raw BGL line}       one line of a replay
#   end    {"kind": "end", "run": ..., "lines": ...}                                the replay is complete: hand out the last windows
#   smoke  {"kind": "smoke", "trace_id": ..., "line": ...}                          the smoke test: classified and stored, but not windowed
# Anything else is counted as bad and skipped.
class Engine:
    def __init__(self, detectors, load_state=None, clock=time.time):
        self.detectors = detectors

        # Called with a run id the first time that run is seen after a start: returns what was stored for it before (or None).
        self.load_state = load_state or (lambda run: None)
        self.clock = clock

        self.run = None
        self.windower = None
        self.handled = {}

    # Handle a batch of Records. Returns an Output.
    def process(self, records):
        out = Output()

        for record in records:
            # Kafka hands a message out again after a crash or a rebalance. One that this engine has already handled is skipped.
            if record.offset <= self.handled.get(record.partition, -1):
                out.counts["redelivered"] += 1
                continue

            self.handled[record.partition] = record.offset
            value = record.value

            if not isinstance(value, dict):
                out.counts["bad"] += 1
            elif value.get("kind") == "log":
                self._log(record, value, out)
            elif value.get("kind") == "end":
                self._end(value, out)
            elif value.get("kind") == "smoke":
                self._smoke(record, value, out)
            else:
                out.counts["bad"] += 1

        if self.run is not None:
            out.run(self.run)["set"].update(self.windower.state())

        return out

    # The offset to commit for each partition after the batch: just after the last message that was handled, but not past the oldest line
    # that an open window still needs (after a restart Kafka hands that line out again, and the window is built again).
    def commit_offsets(self, records):
        last = {}

        for record in records:
            last[record.partition] = max(last.get(record.partition, -1), record.offset)

        held = self.windower.oldest_offset() if self.windower else None
        return {p: (held if held is not None and held <= offset else offset + 1) for p, offset in last.items()}

    # Make `run` the run that is running. The first time after a start, what was stored for it is loaded, so the grid continues.
    def _ensure_run(self, run, out):
        if run == self.run:
            return

        # A new replay began, and the old one never said "end" (its producer was stopped): hand out what is left and mark it.
        # (A replay that ended normally is complete already and keeps its status.)
        if self.run is not None and not self.windower.done:
            for window in self.windower.flush():
                out.windows.append(self._window_document(self.run, window, out))

            out.run(self.run)["set"].update(self.windower.state())
            out.run(self.run)["set"]["status"] = "superseded"

        state = self.load_state(run) or {}
        self.windower = Windower(self.detectors.size_seconds, self.detectors.step_seconds, state.get("origin"), state.get("next_k", 0),
                                 state.get("last_key"), state.get("done", False))
        self.run = run
        out.run(run)["insert"].update(status="running", started_at=self.clock())

    def _log(self, record, value, out):
        run = str(value.get("run") or "")
        parsed = parse_bgl_line(str(value.get("line", "")))

        if not run or parsed is None:
            out.counts["bad"] += 1

            # Only the first few are logged one by one: a file of bad lines must not fill the log.
            if out.counts["bad"] <= 3:
                out.logs.append(("warning", "a message was skipped: no run id or not a BGL line", {"topic": record.topic, "partition": record.partition, "offset": record.offset}))

            return

        self._ensure_run(run, out)
        line = self.detectors.classify(parsed.content)
        out.events.append(self._event_document(run, record, value, parsed, line))
        out.counts["lines"] += 1
        out.counts["alerts"] += int(parsed.is_anomalous)
        out.counts["unseen"] += int(line.template_id == UNSEEN)

        status, windows = self.windower.add(Item(record.offset, parsed.timestamp, line.template_id, int(parsed.is_anomalous), line.keyword, line.llm))

        if status != "ok":
            out.counts[status] += 1

        for window in windows:
            out.windows.append(self._window_document(run, window, out))

    def _end(self, value, out):
        run = str(value.get("run") or "")

        if not run:
            out.counts["bad"] += 1
            return

        self._ensure_run(run, out)

        for window in self.windower.flush():
            out.windows.append(self._window_document(run, window, out))

        # The state is saved now, not only at the end of the batch: the batch may go on with the lines of another replay.
        out.run(run)["set"].update(self.windower.state())
        out.run(run)["set"].update(status="complete", finished_at=self.clock(), expected_lines=int(value.get("lines", 0)))
        out.logs.append(("info", "replay complete", {"run": run}))

    # The smoke test's line is classified and stored like any other, but it does not enter a window: it has nothing to do with the log's
    # timeline, and one stray line must not change the windows of a replay.
    def _smoke(self, record, value, out):
        parsed = parse_bgl_line(str(value.get("line", "")))
        trace = str(value.get("trace_id") or "")

        if parsed is None or not trace:
            out.counts["bad"] += 1
            return

        line = self.detectors.classify(parsed.content)
        event = self._event_document("smoke", record, value, parsed, line)
        event["trace_id"] = trace
        event["smoke"] = True
        out.events.append(event)
        out.counts["smoke"] += 1
        out.logs.append(("info", "stored", {"trace_id": trace, "topic": record.topic, "partition": record.partition, "offset": record.offset,
                                            "template_id": line.template_id, "keyword": line.keyword, "llm": line.llm}))

    # The document of one log line. "alert" and "truth" are the ground truth (the BGL alert tag), kept for the evaluation.
    def _event_document(self, run, record, value, parsed, line):
        return {
            "_id": f"{run}:{record.offset}",
            "run": run,
            "offset": record.offset,
            "seq": value.get("seq"),
            "ts": parsed.timestamp,
            "node": parsed.node,
            "component": parsed.component,
            "level": parsed.level,
            "content": parsed.content[:500],
            "template_id": line.template_id,
            "template": line.template[:300],
            "truth": parsed.alert_tag,
            "alert": int(parsed.is_anomalous),
            "keyword": line.keyword,
            "keyword_rule": line.keyword_rule,
            "llm": line.llm,
        }

    # The document of one window: what is in it, what the ground truth says, and what each detector says.
    def _window_document(self, run, window, out):
        items = window.items
        started = time.perf_counter()
        score = self.detectors.score([i.template_id for i in items])
        seconds = time.perf_counter() - started

        alerts = sum(i.alert for i in items)
        keyword_hits = sum(i.keyword for i in items)
        llm_hits = sum(1 for i in items if i.llm == 1)

        out.seconds.append(seconds)
        out.counts["windows"] += 1
        out.counts["flagged_keyword"] += int(keyword_hits > 0)
        out.counts["flagged_llm"] += int(llm_hits > 0)
        out.counts["flagged_logbert"] += score.flag

        return {
            "_id": f"{run}:{window.start}",
            "run": run,
            "start": window.start,
            "end": window.end,
            "n_messages": len(items),
            "first_offset": min(i.offset for i in items),
            "last_offset": max(i.offset for i in items),
            "n_templates": len({i.template_id for i in items}),
            "n_unseen": sum(1 for i in items if i.template_id == UNSEEN),
            "truth_alerts": alerts,
            "truth": int(alerts > 0),
            "keyword_hits": keyword_hits,
            "keyword": int(keyword_hits > 0),
            "llm_hits": llm_hits,
            "llm": int(llm_hits > 0),
            "logbert": score.flag,
            "logbert_missed": score.missed,
            "logbert_chunks": score.chunks,
            "score_ms": round(seconds * 1000, 1),
        }
