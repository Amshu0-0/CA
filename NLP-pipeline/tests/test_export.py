# scripts/export-nlp-bundle.py on a small made-up BGL log that is run through the NLP project's own preprocessing (Drain, split, windows).
# The trained files (model.pt, rules, decisions) are the real ones of the NLP project; they belong to the real Drain parser, not to the made-up
# log, so the last check of the script (the processor's loader) is shown to REFUSE the made-up parser, and then to ACCEPT the bundle when the
# parser is replaced by the stand-in of the other tests. What this cannot show: that YOUR drain.pkl and YOUR messages.parquet are the ones that
# belong to your model.pt. The script checks that on your computer.

import importlib.util
import json
import re
import shutil
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

import engine
from conftest import PIPELINE, REPO
from test_real_model import FILES, FakeDrain, template_texts

pytestmark = pytest.mark.skipif(not all(p.exists() for p in FILES.values()), reason="the NLP project's results are not in this checkout")

SCRIPT = PIPELINE / "scripts" / "export-nlp-bundle.py"

# a handful of message kinds, as in BGL: a text with numbers that Drain turns into <*>
KINDS = [
    ("INFO", "KERNEL", "instruction cache parity error corrected"),
    ("INFO", "KERNEL", "generating core.{n}"),
    ("INFO", "APP", "ciod: Message code {n} is not 3 or 4294967295"),
    ("WARNING", "KERNEL", "total of {n} ddr error(s) detected and corrected"),
    ("INFO", "MMCS", "idoproxydb hit ASSERT condition: ASSERT expression=0 Source file={n}"),
    ("INFO", "KERNEL", "{n} floating point alignment exceptions"),
]
ALERT = ("FATAL", "KERNEL", "data TLB error interrupt {n}")


def load_script():
    spec = importlib.util.spec_from_file_location("export_bundle", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def made_up_log(path, n=20000):
    rng = np.random.default_rng(11)
    t, lines = 1_117_838_570, []
    alerts = set(range(17000, 17040)) | set(range(18500, 18515)) | set(range(19700, 19710)) | set(range(3000, 3005)) | set(range(13000, 13005))

    for i in range(n):
        t += int(rng.integers(0, 4))
        level, component, text = ALERT if i in alerts else KINDS[int(rng.integers(0, len(KINDS)))]
        node = f"R{int(rng.integers(0, 4)):02d}-M{int(rng.integers(0, 2))}-N{int(rng.integers(0, 8))}-C:J{int(rng.integers(2, 17)):02d}-U{int(rng.integers(1, 12)):02d}"
        moment = datetime.fromtimestamp(t - 7 * 3600, tz=timezone.utc)
        tag = "KERNDTLB" if i in alerts else "-"
        lines.append(f"{tag} {t} {moment:%Y.%m.%d} {node} {moment:%Y-%m-%d-%H.%M.%S}.675872 {node} RAS {component} {level} {text.format(n=int(rng.integers(0, 99999)))}")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return lines


@pytest.fixture(scope="module")
def nlp_root(tmp_path_factory):
    from logad.config import load_config
    from logad.data.preprocess import preprocess

    root = tmp_path_factory.mktemp("nlp")
    (root / "configs").mkdir()
    shutil.copy(REPO / "NLP" / "configs" / "bgl.yaml", root / "configs" / "bgl.yaml")
    made_up_log(root / "data" / "raw" / "BGL.log")

    # the NLP project's preprocessing wants to run from its own folder (its paths are relative)
    patch = pytest.MonkeyPatch()
    patch.chdir(root)

    try:
        preprocess(load_config(root / "configs" / "bgl.yaml"))
    finally:
        patch.undo()

    for name, source in (("logbert/model.pt", FILES["model.pt"]), ("rules/rules.json", FILES["rules.json"]),
                         ("rule_guided/llm_rule_based_decisions.csv", FILES["llm_rule_based_decisions.csv"])):
        (root / "results" / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(source, root / "results" / name)

    shutil.copy(REPO / "NLP" / "results" / "logbert_full.json", root / "results" / "logbert_full.json")
    return root


@pytest.fixture
def standin_parser():
    texts = template_texts()
    patch = pytest.MonkeyPatch()
    patch.setattr(engine.TemplateExtractor, "load", staticmethod(lambda path: FakeDrain(texts)))
    yield
    patch.undo()


def test_the_loader_refuses_a_parser_that_does_not_belong_to_the_model(nlp_root, tmp_path, capsys):
    script = load_script()
    code = script.main(["--nlp-root", str(nlp_root), "--lines", "1500", "--out", str(tmp_path / "bundle")])
    seen = capsys.readouterr()

    assert code == 1
    assert "does not belong to the model" in seen.err and "templates of llm_rule_based_decisions.csv" in seen.err


def run_export(nlp_root, out, *extra):
    script = load_script()
    code = script.main(["--nlp-root", str(nlp_root), "--lines", "1500", "--out", str(out), *extra])
    assert code == 0
    return {name: (out / name).read_text(encoding="utf-8") for name in ("bgl-replay.log", "replay-info.json", "settings.json")}


def test_the_bundle_holds_the_settings_of_the_experiment(nlp_root, tmp_path, standin_parser):
    files = run_export(nlp_root, tmp_path / "bundle")
    settings = json.loads(files["settings.json"])

    assert settings["keyword_top_k"] == 30
    assert settings["logbert"] == {"g": 8, "r": 8, "mask_ratio": 0.5, "full_coverage": True, "seed": 1234}
    assert settings["window"] == {"size_seconds": 300, "step_seconds": 60}

    for name in ("drain.pkl", "model.pt", "rules.json", "llm_rule_based_decisions.csv", "settings.json", "bgl-replay.log", "replay-info.json"):
        assert (tmp_path / "bundle" / name).is_file()


def test_the_replay_file_is_a_slice_of_the_test_period_in_time_order(nlp_root, tmp_path, standin_parser):
    files = run_export(nlp_root, tmp_path / "bundle")
    lines = files["bgl-replay.log"].splitlines()
    table = pd.read_parquet(nlp_root / "data" / "processed" / "bgl" / "messages.parquet")
    test = table[table["split"] == "test"].reset_index(drop=True)

    assert len(lines) == 1500
    times = [int(line.split()[1]) for line in lines]
    assert times == sorted(times)

    # every line is a raw line of the made-up log, and together they are ONE run of consecutive messages of the test period
    raw = (nlp_root / "data" / "raw" / "BGL.log").read_text(encoding="utf-8").splitlines()
    number = {line: i for i, line in enumerate(raw)}
    ids = [number[line] for line in lines]
    position = {line_id: p for p, line_id in enumerate(test["line_id"].tolist())}
    begin = position[ids[0]]

    assert ids == test["line_id"].tolist()[begin: begin + 1500]


def test_the_facts_about_the_replay_file_come_from_the_nlp_code(nlp_root, tmp_path, standin_parser):
    from logad.data.bgl import parse_bgl_line
    from logad.data.windows import count_in_windows, sliding_windows

    files = run_export(nlp_root, tmp_path / "bundle")
    info = json.loads(files["replay-info.json"])
    parsed = [parse_bgl_line(line) for line in files["bgl-replay.log"].splitlines()]
    pairs, _ = sliding_windows(np.array([p.timestamp for p in parsed]), 300, 60)
    labels = np.array([int(p.is_anomalous) for p in parsed])

    assert info["lines"] == 1500 and info["alert_lines"] == int(labels.sum())
    assert info["windows"] == len(pairs) and info["anomalous_windows"] == int((count_in_windows(labels, pairs) > 0).sum())
    assert info["largest_window_lines"] == int((pairs[:, 1] - pairs[:, 0]).max())
    assert 0 < info["anomalous_windows"] < info["windows"]
    assert info["line_source"] == "the raw BGL log"


def test_the_slice_is_placed_where_the_windows_are_most_mixed():
    script = load_script()

    # 100 windows of one message each; windows 40 to 59 are anomalous. A slice of 30 messages holds 30 windows. The best mix is 15 and 15, which
    # the slices starting at 25 and at 45 both give: the earlier one wins.
    starts = np.arange(100)
    ends = starts + 1
    labels = np.zeros(100, dtype=int)
    labels[40:60] = 1

    assert script.choose_start(starts, ends, labels, 0, 100, 30, step=1) == 25

    # a test period that is not longer than the slice is taken whole, and a period without anomalies still gives a slice
    assert script.choose_start(starts, ends, labels, 7, 20, 30) == 7
    assert script.choose_start(starts, ends, np.zeros(100, dtype=int), 0, 100, 30, step=1) == 0


def test_the_lines_are_rebuilt_when_there_is_no_raw_log(nlp_root, tmp_path, standin_parser):
    copy = tmp_path / "nlp"
    shutil.copytree(nlp_root, copy)
    (copy / "data" / "raw" / "BGL.log").unlink()
    files = run_export(copy, tmp_path / "bundle")
    info = json.loads(files["replay-info.json"])

    assert info["line_source"].startswith("rebuilt from messages.parquet")
    assert len(files["bgl-replay.log"].splitlines()) == 1500

    # the rebuilt lines carry the same alert tags and times as the raw ones would
    parsed = [line.split()[:2] for line in files["bgl-replay.log"].splitlines()]
    assert sum(tag != "-" for tag, _ in parsed) == info["alert_lines"]


def test_a_raw_log_that_does_not_match_the_table_is_refused(nlp_root, tmp_path, standin_parser, capsys):
    copy = tmp_path / "nlp"
    shutil.copytree(nlp_root, copy)
    raw = copy / "data" / "raw" / "BGL.log"
    lines = raw.read_text(encoding="utf-8").splitlines()
    lines[:] = [line.replace(" RAS ", " RAS X", 1) if i % 2 == 0 else line for i, line in enumerate(lines)]
    raw.write_text("\n".join(lines) + "\n", encoding="utf-8")

    script = load_script()
    code = script.main(["--nlp-root", str(copy), "--lines", "1500", "--out", str(tmp_path / "bundle")])

    assert code == 1 and "differs from messages.parquet" in capsys.readouterr().err


def test_a_missing_file_is_named_with_the_place_to_look(nlp_root, tmp_path, capsys):
    copy = tmp_path / "nlp"
    shutil.copytree(nlp_root, copy)
    (copy / "results" / "rules" / "rules.json").unlink()

    script = load_script()
    code = script.main(["--nlp-root", str(copy), "--out", str(tmp_path / "bundle")])
    error = capsys.readouterr().err

    assert code == 1 and "rules.json" in error and str(copy) in error and "make rules" in error


# ---------------------------------------------------------------- which run, and where from

# The trained files as the NLP project's own "make restore" lays them out in a working folder outputs/<run>/ (moved, so only one copy exists).
def move_results_to_outputs(root, run):
    for name in ("logbert/model.pt", "rules/rules.json", "rule_guided/llm_rule_based_decisions.csv"):
        target = root / "outputs" / run / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(root / "results" / name), str(target))

    target = root / "outputs" / run / "results" / "logbert_full.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(root / "results" / "logbert_full.json"), str(target))


def test_a_configuration_that_inherits_is_read_like_the_nlp_project_reads_it(nlp_root, tmp_path, standin_parser, capsys, monkeypatch):
    # configs/bgl_cpu.yaml says "extends: bgl.yaml" and changes few things, among them the working folder (outputs/bgl_cpu). The window, the
    # seed and the number of rules are not in it: they must come from bgl.yaml. The files here are only in outputs/bgl_cpu, so they are found
    # only if the inheritance is followed and the name of the configuration is looked for in the NLP folder.
    copy = tmp_path / "nlp"
    shutil.copytree(nlp_root, copy)
    shutil.copy(REPO / "NLP" / "configs" / "bgl_cpu.yaml", copy / "configs" / "bgl_cpu.yaml")
    move_results_to_outputs(copy, "bgl_cpu")
    monkeypatch.chdir(tmp_path)

    run_export(copy, tmp_path / "bundle", "--config", "configs/bgl_cpu.yaml")
    shown = capsys.readouterr().out
    settings = json.loads((tmp_path / "bundle" / "settings.json").read_text(encoding="utf-8"))

    assert settings["from"]["experiment"] == "bgl_cpu"
    assert settings["window"] == {"size_seconds": 300, "step_seconds": 60} and settings["keyword_top_k"] == 30 and settings["logbert"]["seed"] == 1234
    assert "experiment name: bgl_cpu" in shown
    assert str((copy / "outputs" / "bgl_cpu" / "logbert" / "model.pt").resolve()) in shown

    # the same folder with the default configuration (bgl.yaml, working folder outputs/bgl) finds nothing, and says where it looked
    script = load_script()
    code = script.main(["--nlp-root", str(copy), "--out", str(tmp_path / "other")])
    error = capsys.readouterr().err

    assert code == 1 and "model.pt" in error and "outputs/bgl/logbert/model.pt" in error and "bgl_cpu" not in error


def test_a_configuration_that_does_not_exist_is_named(nlp_root, tmp_path, capsys):
    script = load_script()
    code = script.main(["--nlp-root", str(nlp_root), "--config", "configs/nothing.yaml", "--out", str(tmp_path / "bundle")])

    assert code == 1 and "configs/nothing.yaml" in capsys.readouterr().err


def test_the_committed_results_come_first_and_a_different_run_in_outputs_is_reported(nlp_root, tmp_path, standin_parser, capsys):
    copy = tmp_path / "nlp"
    shutil.copytree(nlp_root, copy)
    committed = copy / "results" / "rules" / "rules.json"
    working = copy / "outputs" / "bgl" / "rules" / "rules.json"
    working.parent.mkdir(parents=True)
    working.write_text(json.dumps(json.loads(committed.read_text(encoding="utf-8"))), encoding="utf-8")     # the same rules, written another way
    assert working.read_bytes() != committed.read_bytes()

    run_export(copy, tmp_path / "bundle")
    shown = capsys.readouterr().out

    assert (tmp_path / "bundle" / "rules.json").read_bytes() == committed.read_bytes()
    assert re.search(r"Note: rules\.json .* exists twice and the two differ\. Taken: %s\." % re.escape(str(committed.resolve())), shown)
    assert "--prefer-outputs takes the other one" in shown

    # with --prefer-outputs the other one is taken
    run_export(copy, tmp_path / "second", "--prefer-outputs")
    shown = capsys.readouterr().out

    assert (tmp_path / "second" / "rules.json").read_bytes() == working.read_bytes()
    assert "Taken: %s." % working.resolve() in shown and "you asked for outputs/ with --prefer-outputs" in shown


def test_two_identical_copies_are_not_reported(nlp_root, tmp_path, standin_parser, capsys):
    copy = tmp_path / "nlp"
    shutil.copytree(nlp_root, copy)
    working = copy / "outputs" / "bgl" / "rules" / "rules.json"
    working.parent.mkdir(parents=True)
    shutil.copy(copy / "results" / "rules" / "rules.json", working)

    run_export(copy, tmp_path / "bundle")

    assert "exists twice" not in capsys.readouterr().out


def test_the_files_that_are_used_are_listed(nlp_root, tmp_path, standin_parser, capsys):
    run_export(nlp_root, tmp_path / "bundle")
    shown = capsys.readouterr().out

    for name, path in (("model:", "results/logbert/model.pt"), ("rules:", "results/rules/rules.json"), ("decisions:", "results/rule_guided/llm_rule_based_decisions.csv")):
        assert re.search(r"^  %s\s+\S*%s$" % (name, re.escape(path)), shown, re.M), name


def test_a_python_without_the_nlp_code_is_told_what_to_do(nlp_root, tmp_path, capsys, monkeypatch):
    monkeypatch.setitem(sys.modules, "logad.config", None)
    script = load_script()
    code = script.main(["--nlp-root", str(nlp_root), "--out", str(tmp_path / "bundle")])

    assert code == 1 and "Python environment of your NLP project" in capsys.readouterr().err


def test_make_export_bundle_uses_the_configuration_that_the_committed_results_come_from():
    # NLP/results/README.md names the configuration of the committed run; the Makefile must pass that one, and it must exist
    makefile = (PIPELINE / "Makefile").read_text(encoding="utf-8")
    default = re.search(r"^NLP_CONFIG\s*\?=\s*(\S+)", makefile, re.M).group(1)
    readme = (REPO / "NLP" / "results" / "README.md").read_text(encoding="utf-8")

    assert (REPO / "NLP" / default).is_file()
    assert "configs/bgl_cpu.yaml" in readme and default == "configs/bgl_cpu.yaml"
    assert re.search(r"^export-bundle:\s*;.*--config \$\(NLP_CONFIG\)", makefile, re.M)


# ---------------------------------------------------------------- a run that stops must not touch the bundle

# Found in a real run: the default processed folder held a small sample (another run of make preprocess than the one the trained files come
# from). The loader refused it, as it should, but the files had been copied into bundle/ before it was asked, so drain.pkl and the replay file
# of the earlier good export had been overwritten and the bundle was a mix of two runs - which was committed and pushed.

def standin(texts=None):
    texts = texts or template_texts()
    patch = pytest.MonkeyPatch()
    patch.setattr(engine.TemplateExtractor, "load", staticmethod(lambda path: FakeDrain(texts)))
    return patch


def snapshot(folder):
    return {p.name: p.read_bytes() for p in sorted(folder.iterdir())}


def test_an_export_that_stops_leaves_the_bundle_of_the_last_good_run_as_it_was(nlp_root, tmp_path, capsys):
    out = tmp_path / "bundle"
    good = standin()

    try:
        run_export(nlp_root, out)
    finally:
        good.undo()

    before = snapshot(out)
    capsys.readouterr()

    # another slice; the parser is now the made-up project's own, which does not belong to the trained model, so the last check refuses it
    code = load_script().main(["--nlp-root", str(nlp_root), "--lines", "1000", "--out", str(out)])
    seen = capsys.readouterr()

    assert code == 1 and "does not belong to the model" in seen.err
    assert snapshot(out) == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["bundle"]  # and nothing is left over next to it


def test_a_first_export_that_stops_writes_no_file(nlp_root, tmp_path, capsys):
    out = tmp_path / "bundle"
    code = load_script().main(["--nlp-root", str(nlp_root), "--lines", "1500", "--out", str(out)])

    assert code == 1 and "does not belong to the model" in capsys.readouterr().err
    assert not out.exists() or list(out.iterdir()) == []
    assert [p.name for p in tmp_path.iterdir() if p.name != "bundle"] == []


def test_a_good_export_says_which_files_of_an_earlier_bundle_it_replaced(nlp_root, tmp_path, capsys):
    out = tmp_path / "bundle"
    good = standin()

    try:
        run_export(nlp_root, out)
        first = capsys.readouterr().out
        run_export(nlp_root, out, "--lines", "1000")
        second = capsys.readouterr().out
    finally:
        good.undo()

    assert "Replaced" not in first
    assert re.search(r"Replaced.*bgl-replay\.log", second) and "replay-info.json" in second
    assert "model.pt" not in re.search(r"Replaced[^\n]*", second).group(0)  # the same trained files again: not a change


def test_a_parser_that_does_not_fit_points_to_the_folder_next_to_it_that_does(nlp_root, tmp_path, capsys):
    # the NLP folder of the real run: data/processed/bgl is a small sample, data/processed/bgl_full is the run the trained files come from
    copy = tmp_path / "nlp"
    shutil.copytree(nlp_root, copy)
    processed = copy / "data" / "processed"
    shutil.copytree(processed / "bgl", processed / "bgl_full")
    shutil.copytree(processed / "bgl", processed / "bgl_old")  # another run that does not fit either
    (processed / "bgl_full" / "drain.pkl").write_bytes(b"the parser of the run that the model belongs to")
    (processed / "bgl_incomplete").mkdir()  # a folder without the files of a processed run is not looked at
    (processed / "bgl_incomplete" / "drain.pkl").write_bytes(b"the parser of the run that the model belongs to")
    texts = template_texts()
    patch = pytest.MonkeyPatch()
    patch.setattr(engine.TemplateExtractor, "load", staticmethod(
        lambda path: FakeDrain(texts) if open(path, "rb").read().startswith(b"the parser of the run") else FakeDrain({1: "text that is in no saved decision"})))

    try:
        out = tmp_path / "bundle"
        script = load_script()
        code = script.main(["--nlp-root", str(copy), "--lines", "1500", "--out", str(out)])
        error = capsys.readouterr().err

        assert code == 1 and "does not belong to the model" in error
        assert str(processed / "bgl_full") in error and "--processed" in error and "make export-bundle" in error
        assert "bgl_old" not in error and "bgl_incomplete" not in error  # only a folder that the loader accepts is named
        assert not out.exists() or list(out.iterdir()) == []

        # with the folder that fits, it works, and it says which folder it took
        code = script.main(["--nlp-root", str(copy), "--lines", "1500", "--out", str(out), "--processed", str(processed / "bgl_full")])
        shown = capsys.readouterr().out

        assert code == 0 and "processed data:  " + str(processed / "bgl_full") in shown
        assert (out / "drain.pkl").read_bytes().startswith(b"the parser of the run")
    finally:
        patch.undo()


def test_without_another_folder_that_fits_the_message_does_not_invent_one(nlp_root, tmp_path, capsys):
    code = load_script().main(["--nlp-root", str(nlp_root), "--lines", "1500", "--out", str(tmp_path / "bundle")])
    error = capsys.readouterr().err

    assert code == 1 and "does not belong to the model" in error
    assert "next to it" not in error and "BUNDLE_ARGS" not in error


# ---------------------------------------------------------------- how many lines get another template, and what that means

# A real run (the full BGL log) had 3.3% of the lines with another template than messages.parquet holds, although drain.pkl and templates.csv
# were written together. Drain puts each line into the nearest group while it learns (similarity 0.3); the processor asks the finished tree for
# an exact match. So the percentage alone says nothing about the files; templates.csv does.

def write_templates_csv(folder, texts):
    pd.DataFrame(sorted(texts.items()), columns=["template_id", "template"]).to_csv(folder / "templates.csv", index=False)


def test_lines_with_another_template_are_no_warning_when_templates_csv_and_drain_pkl_agree(nlp_root, tmp_path, capsys):
    copy = tmp_path / "nlp"
    shutil.copytree(nlp_root, copy)
    texts = template_texts()
    write_templates_csv(copy / "data" / "processed" / "bgl", texts)
    patch = standin(texts)

    try:
        run_export(copy, tmp_path / "bundle")
    finally:
        patch.undo()

    shown = capsys.readouterr().out
    agree = int(re.search(r"holds for (\d+) of (\d+) lines", shown).group(1))

    assert agree < 1500  # the stand-in parser knows other templates than the made-up table
    assert "WARNING" not in shown
    assert re.search(r"Note: for \d+ of 1500 lines \(\d+\.\d%\) the parser's template is not the one in messages.parquet", shown)
    assert f"the {len(texts)} templates of drain.pkl are exactly those of templates.csv" in shown


def test_templates_that_differ_from_templates_csv_are_a_warning_whatever_the_percentage(nlp_root, tmp_path, capsys):
    copy = tmp_path / "nlp"
    shutil.copytree(nlp_root, copy)
    texts = template_texts()
    changed = dict(texts)
    first = min(changed)
    changed[first] = changed[first] + " and more"
    write_templates_csv(copy / "data" / "processed" / "bgl", changed)
    patch = standin(texts)

    try:
        run_export(copy, tmp_path / "bundle")
    finally:
        patch.undo()

    shown = capsys.readouterr().out

    assert "WARNING: the templates of drain.pkl differ from templates.csv in 1 of %d places" % len(texts) in shown
    assert "exactly those of templates.csv" not in shown


def test_without_templates_csv_the_old_rule_of_thumb_is_all_there_is(nlp_root, tmp_path, capsys):
    copy = tmp_path / "nlp"
    shutil.copytree(nlp_root, copy)
    (copy / "data" / "processed" / "bgl" / "templates.csv").unlink()
    patch = standin()

    try:
        run_export(copy, tmp_path / "bundle")
    finally:
        patch.undo()

    shown = capsys.readouterr().out

    assert "WARNING: for " in shown and "exactly those of templates.csv" not in shown
