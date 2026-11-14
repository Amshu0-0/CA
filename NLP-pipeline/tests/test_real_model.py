# The engine with the files of the NLP project: the trained LogBERT (model.pt), the ranked keyword rules (rules.json) and the saved decisions
# of the rule-guided LLM (llm_rule_based_decisions.csv). Only Drain is replaced by a small fake that knows the texts of the saved decisions,
# because drain.pkl is made by `make preprocess` on the full BGL file and is not part of the repository.

import csv
import json
import re
import shutil
import time

import numpy as np
import pytest

import engine
from engine import BundleError, Detectors, Engine, UNSEEN
from fakes import bgl_line, record
from logad.logbert.dataset import window_to_chunks
from logad.logbert.trainer import load_checkpoint
from logad.rules.keyword import KeywordClassifier
from logad.rules.rule import load_rules
from sink import MemorySink

from conftest import REPO

RESULTS = REPO / "NLP" / "results"
FILES = {"model.pt": RESULTS / "logbert" / "model.pt", "rules.json": RESULTS / "rules" / "rules.json",
         "llm_rule_based_decisions.csv": RESULTS / "rule_guided" / "llm_rule_based_decisions.csv"}

pytestmark = pytest.mark.skipif(not all(p.exists() for p in FILES.values()), reason="the NLP project's results are not in this checkout")

SETTINGS = {"keyword_top_k": 30, "logbert": {"g": 8, "r": 8, "mask_ratio": 0.5, "full_coverage": True, "seed": 1234},
            "window": {"size_seconds": 300, "step_seconds": 60}}


# Knows every template that the saved decisions and the model's vocabulary mention (texts of the vocabulary-only keys are made up).
class FakeDrain:
    def __init__(self, texts):
        self.templates = dict(texts)
        self.known = {content_of(text): (i, text) for i, text in self.templates.items()}

    def match(self, content):
        found = self.known.get(content)
        return (None, self.mask(content)) if found is None else found

    @staticmethod
    def mask(content):
        return re.sub(r"\d+", "<*>", content)


def content_of(template):
    return template.replace("<*>", "42")


def template_texts(path=FILES["llm_rule_based_decisions.csv"]):
    with open(path, newline="", encoding="utf-8") as f:
        texts = {int(row["template_id"]): row["template"] for row in csv.DictReader(f)}

    _model, vocab, _centre, _checkpoint = load_checkpoint(FILES["model.pt"], "cpu")

    for key in vocab.keys:
        texts.setdefault(key, f"made up template number {key}")

    return texts


def make_bundle(directory, texts):
    directory.mkdir(parents=True, exist_ok=True)

    for name, source in FILES.items():
        shutil.copy(source, directory / name)

    (directory / "settings.json").write_text(json.dumps(SETTINGS))
    (directory / "drain.pkl").write_bytes(b"placeholder: the test replaces TemplateExtractor.load")


@pytest.fixture(scope="module")
def detectors(tmp_path_factory):
    directory = tmp_path_factory.mktemp("bundle")
    make_bundle(directory, None)
    texts = template_texts()

    patch = pytest.MonkeyPatch()
    patch.setattr(engine.TemplateExtractor, "load", staticmethod(lambda path: FakeDrain(texts)))

    try:
        yield Detectors.load(directory)
    finally:
        patch.undo()


def test_the_bundle_loads_with_the_settings_of_the_nlp_project(detectors):
    assert (detectors.g, detectors.r) == (8, 8)
    assert (detectors.size_seconds, detectors.step_seconds) == (300, 60)
    assert detectors.logbert.full_coverage is True and detectors.logbert.mask_ratio == 0.5 and detectors.logbert.seed == 1234
    assert len(detectors.keyword.rules) == 30
    assert len(detectors.logbert.vocab) == 176


def test_one_line_gets_the_same_verdicts_as_the_nlp_code_gives(detectors):
    rules = load_rules(FILES["rules.json"])[:30]
    keyword = KeywordClassifier(rules)
    saved = {i: (label, text) for i, (label, text) in engine.read_llm_decisions(FILES["llm_rule_based_decisions.csv"]).items()}
    checked = hits = 0

    for template_id, (label, text) in sorted(saved.items()):
        line = detectors.classify(content_of(text))
        expected = keyword.classify(template_id, text)

        assert line.template_id == template_id and line.template == text
        assert (line.keyword, line.keyword_rule) == (int(expected.label), expected.rule)
        assert line.llm == label
        checked += 1
        hits += line.keyword

    assert checked == len(saved) > 400 and hits > 0


def test_a_line_that_matches_no_template_is_unseen_and_judged_by_its_masked_text(detectors):
    line = detectors.classify("a message nobody has ever seen 12345 data TLB error interrupt")

    assert line.template_id == UNSEEN and line.llm is None
    assert line.template == "a message nobody has ever seen <*> data TLB error interrupt"
    assert line.keyword == int(KeywordClassifier(load_rules(FILES["rules.json"])[:30]).classify(UNSEEN, line.template).label)

    plain = detectors.classify("a message that says nothing special 7")
    assert plain.template_id == UNSEEN and plain.keyword == 0 and plain.keyword_rule is None


def test_a_window_is_scored_like_the_nlp_code_scores_one_window(detectors):
    detector = detectors.logbert
    rng = np.random.default_rng(3)

    for length in (1, 2, 5, 37, 300, 511, 512, 700, 1200):
        ids = rng.choice(detector.vocab.keys, size=length)
        score = detectors.score(ids)

        # the batch function of the evaluation, asked about this one window (its masks are seeded with index 0, as ours are)
        import pandas as pd

        windows = pd.DataFrame({"start_idx": [0], "end_idx": [length]})
        batch = detector.score_windows(ids, windows)
        assert score.missed == int(batch.max_misses(8)[0]), length
        assert score.flag == int(batch.predict(8, 8)[0]) and score.chunks == len(window_to_chunks(detector.vocab.encode(ids), 512))


def test_the_scoring_batch_is_small_and_does_not_change_the_answer(detectors):
    # A very busy window must not need more memory than the processor may use (processor.yaml): the batch is small ...
    assert detectors.logbert.batch_size == engine.SCORE_BATCH == 16

    # ... and the answer does not depend on it: the masks are seeded by the position of the chunk, not by the batch.
    ids = np.random.default_rng(8).choice(detectors.logbert.vocab.keys, size=2300)
    small = detectors.score(ids)
    detectors.logbert.batch_size = 128

    try:
        assert detectors.score(ids) == small
    finally:
        detectors.logbert.batch_size = engine.SCORE_BATCH


def test_the_same_window_always_gets_the_same_answer(detectors):
    ids = np.random.default_rng(4).choice(detectors.logbert.vocab.keys, size=250)
    first = detectors.score(ids)

    for _ in range(3):
        detectors.score(np.random.default_rng(5).choice(detectors.logbert.vocab.keys, size=60))
        assert detectors.score(ids) == first


def test_unseen_templates_always_count_as_missed_keys(detectors):
    assert detectors.score([UNSEEN] * 20).missed == 20 and detectors.score([UNSEEN] * 20).flag == 1
    assert detectors.score([UNSEEN] * 3).missed == 3 and detectors.score([UNSEEN] * 3).flag == 0
    assert detectors.score([]) == engine.Score(0, 0, 0)


def test_the_longest_windows_of_the_log_are_scored_in_time(detectors):
    ids = np.random.default_rng(6).choice(detectors.logbert.vocab.keys, size=1500)
    started = time.perf_counter()
    score = detectors.score(ids)

    assert score.chunks == 3 and time.perf_counter() - started < 5


def test_a_whole_replay_with_the_real_detectors(detectors):
    texts = detectors.extractor.templates
    keys = detectors.logbert.vocab.keys
    rng = np.random.default_rng(7)
    ts, records = 1_118_000_000, []

    for offset in range(1500):
        ts += int(rng.integers(0, 3))
        tag = "KERNDTLB" if offset in (400, 401, 1200) else "-"
        line = bgl_line(ts, 0, tag).split(" t0")[0] + " " + content_of(texts[int(rng.choice(keys))])
        records.append(record(offset, {"kind": "log", "run": "demo", "seq": offset, "line": line}))

    records.append(record(1500, {"kind": "end", "run": "demo", "lines": 1500}))
    sink, machine = MemorySink(), Engine(detectors, MemorySink().load_state, lambda: 5.0)

    for start in range(0, len(records), 200):
        sink.write(machine.process(records[start: start + 200]))

    assert len(sink.events) == 1500 and len(sink.windows) > 20
    assert sink.runs["demo"]["status"] == "complete"
    assert sum(w["n_messages"] for w in sink.windows.values()) >= 1500
    assert all(w["logbert_chunks"] >= 1 and w["score_ms"] >= 0 for w in sink.windows.values())
    assert {w["truth"] for w in sink.windows.values()} == {0, 1}

    for event in sink.events.values():
        assert event["template_id"] in texts and event["truth"] in ("-", "KERNDTLB")


def test_the_bundle_must_belong_together(tmp_path):
    texts = template_texts()
    patch = pytest.MonkeyPatch()
    patch.setattr(engine.TemplateExtractor, "load", staticmethod(lambda path: FakeDrain(texts)))

    try:
        # a drain.pkl from another preprocessing run: other templates behind the same IDs
        other = {i: text + " (changed)" if i % 2 else text for i, text in texts.items()}
        patch.setattr(engine.TemplateExtractor, "load", staticmethod(lambda path: FakeDrain(other)))
        make_bundle(tmp_path / "wrong", None)

        with pytest.raises(BundleError, match="does not belong"):
            Detectors.load(tmp_path / "wrong")

        # a drain.pkl that lacks keys that the model knows
        fewer = {i: t for i, t in texts.items() if i not in (1, 2, 3)}
        patch.setattr(engine.TemplateExtractor, "load", staticmethod(lambda path: FakeDrain(fewer)))

        with pytest.raises(BundleError, match="keys of the LogBERT vocabulary"):
            Detectors.load(tmp_path / "wrong")

        # the right one loads
        patch.setattr(engine.TemplateExtractor, "load", staticmethod(lambda path: FakeDrain(texts)))
        assert Detectors.load(tmp_path / "wrong").g == 8
    finally:
        patch.undo()


def test_a_missing_file_says_which_one_and_what_to_run(tmp_path):
    make_bundle(tmp_path / "b", None)
    (tmp_path / "b" / "rules.json").unlink()

    with pytest.raises(BundleError, match=r"rules\.json is missing.*export-nlp-bundle"):
        Detectors.load(tmp_path / "b")
