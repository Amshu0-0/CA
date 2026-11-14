# scripts/local-replay.py with the real LogBERT, rules and decisions of the NLP project (Drain replaced by the stand-in of test_real_model.py).
# The numbers of the keyword rules and of the saved LLM decisions in the table are recalculated here in a different way (the NLP project's own
# window function and a prefix sum) and must be the same. LogBERT is not recalculated: the NLP project's batch evaluation seeds its masks by the
# position of a window in the whole batch, the pipeline seeds them per window, so a borderline window can come out differently (README says so).

import importlib.util
import json

import numpy as np
import pytest

import engine
from conftest import PIPELINE
from fakes import bgl_line
from logad.data.bgl import parse_bgl_line
from logad.data.windows import count_in_windows, sliding_windows
from test_real_model import FILES, FakeDrain, content_of, make_bundle, template_texts

pytestmark = pytest.mark.skipif(not all(p.exists() for p in FILES.values()), reason="the NLP project's results are not in this checkout")


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), PIPELINE / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def bundle(tmp_path):
    texts = template_texts()
    make_bundle(tmp_path, texts)
    keys = sorted(texts)
    rng = np.random.default_rng(21)
    ts, lines = 1_118_000_000, []

    for i in range(1400):
        ts += int(rng.integers(0, 3)) if i % 400 else 900
        tag = "KERNDTLB" if i in (200, 201, 202, 900, 1300) else "-"
        template = int(rng.choice(keys))
        lines.append(bgl_line(ts, 0, tag).split(" t0")[0] + " " + content_of(texts[template]))

    (tmp_path / "bgl-replay.log").write_text("\n".join(lines) + "\n", encoding="utf-8")

    patch = pytest.MonkeyPatch()
    patch.setattr(engine.TemplateExtractor, "load", staticmethod(lambda path: FakeDrain(texts)))
    yield tmp_path, lines
    patch.undo()


def expected_counts(lines, detectors, field):
    parsed = [parse_bgl_line(line) for line in lines]
    verdicts = [detectors.classify(p.content) for p in parsed]
    said = np.array([getattr(v, field) == 1 for v in verdicts], dtype=np.int64)
    truth = np.array([int(p.is_anomalous) for p in parsed], dtype=np.int64)
    pairs, _ = sliding_windows(np.array([p.timestamp for p in parsed]), 300, 60)
    flagged, real = count_in_windows(said, pairs) > 0, count_in_windows(truth, pairs) > 0

    return {"tp": int((flagged & real).sum()), "fp": int((flagged & ~real).sum()), "tn": int((~flagged & ~real).sum()), "fn": int((~flagged & real).sum())}, len(pairs), int(real.sum())


def test_the_table_of_the_replay_is_the_one_the_nlp_functions_give(bundle, tmp_path, capsys):
    folder, lines = bundle
    script = load("local-replay")
    saved = tmp_path / "evidence" / "local.json"

    assert script.main(["--bundle", str(folder), "--save", str(saved)]) == 0
    shown = capsys.readouterr().out
    summary = json.loads(saved.read_text(encoding="utf-8"))
    detectors = engine.Detectors.load(str(folder))

    for field in ("keyword", "llm"):
        counts, windows, anomalous = expected_counts(lines, detectors, field)
        got = summary["detectors"][field]

        assert {k: got[k] for k in ("tp", "fp", "tn", "fn")} == counts, field
        assert summary["windows"] == windows and summary["anomalous_windows"] == anomalous

    assert summary["lines"]["stored"] == 1400 and summary["lines"]["with_alert_tag"] == 5
    assert summary["run"]["status"] == "complete"
    assert summary["detectors"]["logbert"]["tp"] + summary["detectors"]["logbert"]["fn"] == summary["anomalous_windows"]

    for name in ("LogBERT, full coverage (A+)", "rule-guided LLM, saved decisions (B3)", "keyword rules (B4)"):
        assert name in shown

    assert "Replay local" in shown and "status: complete" in shown


def test_the_replay_info_of_the_export_is_compared_with_what_the_engine_built(bundle, capsys):
    folder, lines = bundle
    script = load("local-replay")
    export = load("export-nlp-bundle")
    import pandas as pd

    parsed = [parse_bgl_line(line) for line in lines]
    rows = pd.DataFrame({"timestamp": [p.timestamp for p in parsed], "label": [int(p.is_anomalous) for p in parsed]})
    info = export.describe(rows, 300, 60)
    (folder / "replay-info.json").write_text(json.dumps(info), encoding="utf-8")

    assert script.main(["--bundle", str(folder)]) == 0
    assert "the NLP code expects from this file" in capsys.readouterr().out

    # a replay-info.json that says something else makes the script fail, and say what differs
    info["windows"] += 1
    (folder / "replay-info.json").write_text(json.dumps(info), encoding="utf-8")

    assert script.main(["--bundle", str(folder)]) == 1
    assert "replay-info.json says windows" in capsys.readouterr().err


def test_a_missing_replay_file_is_named(tmp_path, capsys):
    script = load("local-replay")

    assert script.main(["--bundle", str(tmp_path)]) == 1
    assert "make export-bundle" in capsys.readouterr().err


def test_the_two_summaries_are_compared_number_by_number(bundle, tmp_path):
    folder, _ = bundle
    script, tables = load("local-replay"), load("tables")
    first, second = tmp_path / "a.json", tmp_path / "b.json"

    assert script.main(["--bundle", str(folder), "--save", str(first)]) == 0
    a = json.loads(first.read_text(encoding="utf-8"))
    b = json.loads(first.read_text(encoding="utf-8"))

    assert tables.differences(a, b) == []

    b["detectors"]["keyword"]["fp"] += 2
    b["windows"] += 1

    assert tables.differences(a, b, ("laptop", "cluster")) == ["windows: laptop %d, cluster %d" % (a["windows"], b["windows"]), "keyword rules (B4): fp laptop %d, cluster %d" % (a["detectors"]["keyword"]["fp"], b["detectors"]["keyword"]["fp"])]
