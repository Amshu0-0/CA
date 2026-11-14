# Collects everything that the pipeline needs from your NLP project into NLP-pipeline/bundle/, and checks that it belongs together.
#
#   make export-bundle                                     (run it in the Python environment of your NLP project)
#   python3 NLP-pipeline/scripts/export-nlp-bundle.py --nlp-root NLP --lines 30000
#
# What ends up in bundle/ (the processor image holds the first five, the producer image holds the replay file):
#   drain.pkl                        the fitted Drain parser: log text -> template ID            from  data/processed/bgl/
#   model.pt                         the trained LogBERT                                          from  results/logbert/ or outputs/<run>/logbert/
#   rules.json                       the ranked keyword rules (condition B4)                      from  results/rules/ or outputs/<run>/rules/
#   llm_rule_based_decisions.csv     the saved decisions of the rule-guided LLM (condition B3)    from  results/rule_guided/ or outputs/<run>/rule_guided/
#   settings.json                    g, r, mask ratio, seed, window size, number of rules: the values of your experiment
#   bgl-replay.log                   the log lines to replay: a slice of the TEST period of your split, in time order, as BGL lines
#   replay-info.json                 facts about that slice (lines, windows, anomalous windows, largest window), for the checks
#
# The configuration says where the processed data and the working folder outputs/ are, and the window, the seed and the number of rules. It is
# read with the NLP project's own loader, so one that inherits from another (configs/bgl_cpu.yaml extends configs/bgl.yaml) works: make
# export-bundle uses the one that the committed results come from (see NLP/results/README.md).
#
# results/ is the run that your report describes (it is what "make results" copies there and what is committed), so it is taken first. A run that
# is only in outputs/ (not copied to results/ yet) is taken with --prefer-outputs. If both exist and differ, the script says so.
#
# The replay file: a run of consecutive messages of the test period (the period that the detectors were NOT tuned on), 30,000 lines by
# default, placed where the windows are most evenly mixed between anomalous and normal ones (so the three detectors have something to find
# and something to get wrong). --start N takes the slice that begins at message N of the test period instead. The lines are the raw BGL
# lines if data/raw/BGL.log is there, otherwise they are rebuilt from messages.parquet in the same layout; either way every line is parsed
# again and compared with messages.parquet (timestamp, alert tag, node, component, level, text) before the file is written.
#
# At the end the processor's own loader reads the bundle (the same code the processor runs at start-up). It stops with a clear message if
# drain.pkl does not belong to model.pt and the decisions, which would make every verdict wrong without any error.
#
# Nothing in your NLP folder is changed. Needs: pandas, pyarrow, PyYAML, numpy and torch (the environment of your NLP project).

import argparse
import filecmp
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
PIPELINE = HERE.parent
REPO = PIPELINE.parent

# The columns of messages.parquet that the replay file is made from (written by logad.data.bgl.read_bgl and logad.data.preprocess).
COLUMNS = ["line_id", "timestamp", "node", "component", "level", "content", "alert_tag", "label", "template_id", "split"]

# A window of the replay with more lines than this is reported (the processor handles much more, see processor.yaml, but it is worth knowing).
BIG_WINDOW = 20000


class Problem(Exception):
    pass


def say(text=""):
    print(text, flush=True)


# ---------------------------------------------------------------- where the files are

# The first of the candidate files that exists. The message names every place that was looked at.
def first_existing(candidates, what, hint):
    for path in candidates:
        if path.is_file():
            return path

    raise Problem(f"{what} was not found. Looked in:\n  " + "\n  ".join(str(p) for p in candidates) + f"\n{hint}")


# The committed file and the one in the working folder outputs/ can both exist. The committed one (the run of the report) goes first unless
# prefer_outputs is set. A pair that differs is noted in `differs`, so that nobody takes the wrong model without knowing.
def pick(committed, working, what, hint, prefer_outputs, differs):
    order = [working, committed] if prefer_outputs else [committed, working]
    chosen = first_existing(order, what, hint)
    other = next((p for p in order if p != chosen and p.is_file()), None)

    if other is not None and not filecmp.cmp(chosen, other, shallow=False):
        differs.append((what, chosen, other))

    return chosen


def locate(args):
    # the NLP project's own loader: it follows "extends:" (configs/bgl_cpu.yaml inherits from configs/bgl.yaml)
    try:
        from logad.config import config_to_dict, load_config
    except ImportError as error:
        raise Problem(f"the code of the NLP project cannot be imported ({error}). Run this in the Python environment of your NLP project "
                      "(the one in which  make preprocess  works) and give the NLP folder with --nlp-root.") from None

    nlp = Path(args.nlp_root).resolve()
    config_path = Path(args.config) if args.config else nlp / "configs" / "bgl.yaml"

    # a configuration named like  configs/bgl_cpu.yaml  is looked for in the NLP folder when it is not there relative to where you are
    if not config_path.is_file() and not config_path.is_absolute() and (nlp / config_path).is_file():
        config_path = nlp / config_path

    config_path = config_path.resolve()

    if not config_path.is_file():
        raise Problem(f"the experiment configuration {config_path} does not exist. Give the NLP folder with --nlp-root and the file with --config")

    config = config_to_dict(load_config(config_path))
    paths = config["paths"]
    output = nlp / paths["output_dir"]
    results = nlp / "results"
    processed = Path(args.processed).resolve() if args.processed else nlp / paths["processed_dir"]
    raw = Path(args.raw_log).resolve() if args.raw_log else nlp / paths["raw_log"]
    again = f"Run  make preprocess  in {nlp}  (it needs data/raw/BGL.log: make data), or copy the folder {paths['processed_dir']} from the computer where you ran it."
    differs = []
    prefer = bool(args.prefer_outputs)

    found = {
        "config": config,
        "config_path": config_path,
        "processed": processed,
        "raw": raw,
        "differs": differs,
        "drain": first_existing([processed / "drain.pkl"], "drain.pkl (the fitted Drain parser)", again),
        "messages": first_existing([processed / "messages.parquet"], "messages.parquet (the processed log)", again),
        "windows": first_existing([processed / "windows.parquet"], "windows.parquet (the windows of the NLP project)", again),
        "model": pick(results / "logbert" / "model.pt", output / "logbert" / "model.pt", "model.pt (the trained LogBERT)",
                      "Train it with  make logbert-train  in the NLP folder, or  make restore  to copy the committed results back.", prefer, differs),
        "rules": pick(results / "rules" / "rules.json", output / "rules" / "rules.json", "rules.json (the ranked keyword rules)",
                      "Make them with  make rules  in the NLP folder, or  make restore  to copy the committed results back.", prefer, differs),
        "decisions": pick(results / "rule_guided" / "llm_rule_based_decisions.csv", output / "rule_guided" / "llm_rule_based_decisions.csv",
                          "llm_rule_based_decisions.csv (the saved decisions of the rule-guided LLM)",
                          "Make them with  make rule-guided  in the NLP folder, or  make restore  to copy the committed results back.", prefer, differs),
        "logbert_result": pick(results / "logbert_full.json", output / "results" / "logbert_full.json", "logbert_full.json (the tuned g and r of LogBERT with full coverage)",
                               "Make it with  make logbert-eval  in the NLP folder, or  make restore  to copy the committed results back.", prefer, differs),
    }

    return found


# ---------------------------------------------------------------- the settings

# The values of the experiment that the processor must use. g, r, the mask ratio and full coverage are what the evaluation chose on the
# validation period (logbert_full.json); the seed, the window and the number of rules come from the configuration.
def make_settings(config, result):
    chosen = result["settings"]
    detection = config["logbert"]["detection"]

    if not chosen.get("full_coverage"):
        raise Problem("logbert_full.json is not the full-coverage variant (A+): its settings say full_coverage = false")

    return {
        "keyword_top_k": int(config["rules"]["top_k"]),
        "logbert": {"g": int(chosen["g"]), "r": int(chosen["r"]), "mask_ratio": float(chosen["mask_ratio"]), "full_coverage": True, "seed": int(detection["seed"])},
        "window": {"size_seconds": int(config["windows"]["size_seconds"]), "step_seconds": int(config["windows"]["step_seconds"])},
        "from": {"experiment": config.get("name"), "logbert_full_test_f1": round(float(result.get("test", {}).get("f1", 0.0)), 4)},
    }


# ---------------------------------------------------------------- the slice

# The first message of the slice, as an index into the test period. The slice [start, start + lines) is chosen where the whole windows
# inside it are most evenly mixed: the largest min(anomalous windows, normal windows). Ties go to the earlier slice.
# `starts` and `ends` are the [start_idx, end_idx) of the test windows (sorted), `labels` their ground truth, `first` the index of the first
# test message and `count` the number of test messages.
def choose_start(starts, ends, labels, first, count, lines, step=500):
    import numpy as np

    if count <= lines:
        return first

    prefix = np.concatenate([[0], np.cumsum(np.asarray(labels, dtype=np.int64))])
    best, best_score = first, -1

    for begin in range(first, first + count - lines + 1, step):
        low = int(np.searchsorted(starts, begin, side="left"))
        high = int(np.searchsorted(ends, begin + lines, side="right"))

        if high <= low:
            continue

        anomalous = int(prefix[high] - prefix[low])
        score = min(anomalous, (high - low) - anomalous)

        if score > best_score:
            best, best_score = begin, score

    return best


# Read the rows [begin, end) of messages.parquet, all columns of COLUMNS that exist, without loading the whole file.
def read_rows(path, begin, end):
    import pandas as pd
    import pyarrow.parquet as pq

    file = pq.ParquetFile(path)
    names = [c for c in COLUMNS if c in file.schema_arrow.names]
    frames, row = [], 0

    for group in range(file.num_row_groups):
        size = file.metadata.row_group(group).num_rows

        if row + size > begin and row < end:
            frame = file.read_row_group(group, columns=names).to_pandas()
            frames.append(frame.iloc[max(begin - row, 0): min(end - row, size)])

        row += size

    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=names)


# ---------------------------------------------------------------- the lines

# One BGL line from the columns of messages.parquet. The fields that no detector reads (the date, the time, the second copy of the node
# and the type) are rebuilt from the epoch.
def rebuild_line(row):
    moment = datetime.fromtimestamp(int(row["timestamp"]), tz=timezone.utc)
    date, clock = moment.strftime("%Y.%m.%d"), moment.strftime("%Y-%m-%d-%H.%M.%S.000000")

    return f"{row['alert_tag']} {int(row['timestamp'])} {date} {row['node']} {clock} {row['node']} RAS {row['component']} {row['level']} {row['content']}"


# The raw lines of the file with these 0-based line numbers (line_id of messages.parquet), in the order of the numbers.
def read_raw_lines(path, line_ids):
    wanted = sorted(set(int(i) for i in line_ids))
    found, position = {}, 0

    with open(path, encoding="utf-8", errors="replace") as f:
        for number, line in enumerate(f):
            if position >= len(wanted):
                break

            if number == wanted[position]:
                found[number] = line.rstrip("\r\n")
                position += 1

    if len(found) != len(wanted):
        raise Problem(f"{path} has fewer lines than messages.parquet says ({len(wanted) - len(found)} of the {len(wanted)} needed lines are missing)")

    return [found[int(i)] for i in line_ids]


# Parse every line again with the NLP project's own parser and compare it with the row it was made from.
def check_lines(lines, rows):
    from logad.data.bgl import parse_bgl_line

    for position, (line, (_, row)) in enumerate(zip(lines, rows.iterrows())):
        parsed = parse_bgl_line(line)

        if parsed is None:
            raise Problem(f"line {position + 1} of the replay file cannot be parsed: {line[:120]}")

        wrong = [name for name, a, b in (("timestamp", parsed.timestamp, int(row["timestamp"])), ("alert tag", parsed.alert_tag, str(row["alert_tag"])),
                                         ("node", parsed.node, str(row["node"])), ("component", parsed.component, str(row["component"])),
                                         ("level", parsed.level, str(row["level"])), ("text", parsed.content, str(row["content"]).strip())) if a != b]

        if wrong:
            raise Problem(f"line {position + 1} of the replay file differs from messages.parquet in: {', '.join(wrong)}. "
                          "If the raw log is another download than the one that was preprocessed, move it away: the lines are then rebuilt from messages.parquet.")


# The facts about the replay file that the checks compare with the cluster: the windows of the NLP code's own function on the file's times.
def describe(rows, size, step):
    import numpy as np

    from logad.data.windows import count_in_windows, sliding_windows

    timestamps = rows["timestamp"].to_numpy(dtype=np.int64)
    labels = rows["label"].to_numpy(dtype=np.int64)
    pairs, _ = sliding_windows(timestamps, size, step)
    anomalous = count_in_windows(labels, pairs) > 0 if len(pairs) else np.zeros(0, dtype=bool)

    return {
        "lines": int(len(rows)),
        "alert_lines": int(labels.sum()),
        "first_timestamp": int(timestamps[0]),
        "last_timestamp": int(timestamps[-1]),
        "windows": int(len(pairs)),
        "anomalous_windows": int(anomalous.sum()),
        "largest_window_lines": int((pairs[:, 1] - pairs[:, 0]).max()) if len(pairs) else 0,
        "grid": {"size_seconds": int(size), "step_seconds": int(step)},
    }


# ---------------------------------------------------------------- the check at the end

# The processor's own loader, then Drain's answer for every line of the replay file against the template that messages.parquet holds.
def check_bundle(directory, rows):
    sys.path.insert(0, str(PIPELINE / "processor"))

    from engine import BundleError, Detectors

    try:
        detectors = Detectors.load(directory)
    except BundleError as error:
        raise Problem(str(error)) from None

    agree = unseen = 0

    for content, template_id in zip(rows["content"], rows["template_id"]):
        line = detectors.classify(str(content).strip())
        agree += int(line.template_id == int(template_id))
        unseen += int(line.template_id == -1)

    return detectors, agree, unseen


# ---------------------------------------------------------------- main

def main(argv=None):
    parser = argparse.ArgumentParser(description="Collect the trained models and a replay file from the NLP project into NLP-pipeline/bundle/.")
    parser.add_argument("--nlp-root", default=str(REPO / "NLP"), help="the folder of the NLP project (default: the folder NLP next to NLP-pipeline)")
    parser.add_argument("--config", help="the experiment configuration, for example configs/bgl_cpu.yaml (default: <nlp-root>/configs/bgl.yaml). One that "
                                         "inherits from another (extends:) is read like the NLP project reads it")
    parser.add_argument("--prefer-outputs", action="store_true", help="take model.pt, rules and decisions from outputs/ (a run not yet copied to results/) when both exist")
    parser.add_argument("--processed", help="the folder with messages.parquet and drain.pkl (default: paths.processed_dir of the configuration)")
    parser.add_argument("--raw-log", help="the raw BGL.log (default: paths.raw_log of the configuration; if it is missing the lines are rebuilt)")
    parser.add_argument("--lines", type=int, default=30000, help="how many lines the replay file holds (default 30000)")
    parser.add_argument("--start", type=int, help="the replay file begins at this message of the test period (default: where the windows are most evenly mixed)")
    parser.add_argument("--out", default=str(PIPELINE / "bundle"), help="where to write (default: NLP-pipeline/bundle)")
    args = parser.parse_args(argv)

    try:
        return run(args)
    except Problem as problem:
        print(f"\nNot done: {problem}", file=sys.stderr)
        return 1


def run(args):
    import numpy as np
    import pandas as pd

    nlp = Path(args.nlp_root).resolve()
    sys.path.insert(0, str(nlp / "src"))

    if args.lines < 100:
        raise Problem("--lines must be at least 100")

    where = locate(args)
    config = where["config"]
    settings = make_settings(config, json.loads(where["logbert_result"].read_text(encoding="utf-8")))
    size, step = settings["window"]["size_seconds"], settings["window"]["step_seconds"]

    say("Your NLP project:  " + str(nlp))
    say("  configuration:   %s (experiment name: %s)" % (where["config_path"], config.get("name")))
    say("  processed data:  " + str(where["processed"]))

    for name in ("model", "rules", "decisions"):
        say("  %-16s %s" % (name + ":", where[name]))

    say("  LogBERT:         g=%d, r=%d, mask ratio %.1f, full coverage, seed %d" % (settings["logbert"]["g"], settings["logbert"]["r"], settings["logbert"]["mask_ratio"], settings["logbert"]["seed"]))
    say("  keyword rules:   the top %d" % settings["keyword_top_k"])
    say("  windows:         %d seconds, sliding by %d seconds" % (size, step))

    for what, chosen, other in where["differs"]:
        say(f"\nNote: {what} exists twice and the two differ. Taken: {chosen}. Not taken: {other}. "
            + ("(you asked for outputs/ with --prefer-outputs)" if args.prefer_outputs else "results/ is the run of your report; --prefer-outputs takes the other one"))

    # The test period: its messages are one run of rows (the split is by time and the table is sorted by time).
    light = pd.read_parquet(where["messages"], columns=["split", "label"])
    in_test = np.flatnonzero(light["split"].to_numpy() == "test")
    del light

    if in_test.size == 0:
        raise Problem("messages.parquet has no message in the test period")

    first, count = int(in_test[0]), int(in_test.size)

    if count != int(in_test[-1]) - first + 1:
        raise Problem("the messages of the test period are not one run of rows: messages.parquet is not sorted by time")

    table = pd.read_parquet(where["windows"])
    test_windows = table[table["split"] == "test"].sort_values("start_idx")

    if args.start is not None:
        if not 0 <= args.start < count:
            raise Problem(f"--start must be between 0 and {count - 1} (the test period has {count} messages)")

        begin = first + args.start
    else:
        begin = choose_start(test_windows["start_idx"].to_numpy(), test_windows["end_idx"].to_numpy(), test_windows["label"].to_numpy(), first, count, args.lines)

    end = min(begin + args.lines, first + count)
    rows = read_rows(where["messages"], begin, end)

    if len(rows) != end - begin:
        raise Problem(f"read {len(rows)} rows of messages.parquet, expected {end - begin}")

    say(f"\nThe test period has {count} messages. The replay file takes the {len(rows)} messages {begin - first} to {end - first - 1} of it.")

    # The lines.
    if where["raw"].is_file():
        lines, origin = read_raw_lines(where["raw"], rows["line_id"].tolist()), "the raw BGL log"
    else:
        lines, origin = [rebuild_line(row) for _, row in rows.iterrows()], "rebuilt from messages.parquet (the raw log is not here)"

    check_lines(lines, rows)
    facts = describe(rows, size, step)
    facts.update({"source": f"messages {begin - first} to {end - first - 1} of the test period of {config.get('name', 'the experiment')}", "line_source": origin})

    # The files.
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)

    for name, source in (("drain.pkl", where["drain"]), ("model.pt", where["model"]), ("rules.json", where["rules"]), ("llm_rule_based_decisions.csv", where["decisions"])):
        shutil.copyfile(source, out / name)

    (out / "settings.json").write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    (out / "bgl-replay.log").write_text("\n".join(lines) + "\n", encoding="utf-8")

    # The check: the processor's loader, and Drain against the table.
    detectors, agree, unseen = check_bundle(out, rows)
    facts["template_ids_agree"] = {"lines": int(len(rows)), "same_as_messages_parquet": int(agree), "unseen": int(unseen)}
    (out / "replay-info.json").write_text(json.dumps(facts, indent=2) + "\n", encoding="utf-8")

    say("\nWritten to " + str(out) + ":")

    for name in ("drain.pkl", "model.pt", "rules.json", "llm_rule_based_decisions.csv", "settings.json", "bgl-replay.log", "replay-info.json"):
        say("  %-32s %8.1f KB" % (name, (out / name).stat().st_size / 1024))

    say("\nThe replay file")
    say(f"  lines:            {facts['lines']}   (from {origin})")
    say(f"  alert lines:      {facts['alert_lines']}   (the dataset's own alert tags, the ground truth)")
    say(f"  windows:          {facts['windows']}, of which anomalous: {facts['anomalous_windows']}   (5-minute windows sliding by 1 minute, the grid starts at the first line of the file)")
    say(f"  largest window:   {facts['largest_window_lines']} lines")
    say(f"  Drain check:      the parser gives the template that messages.parquet holds for {agree} of {len(rows)} lines ({unseen} lines match no template)")
    say("  the bundle loads: the models, the rules and the decisions belong together (the processor's own loader accepted them)")

    if facts["anomalous_windows"] == 0 or facts["anomalous_windows"] == facts["windows"]:
        say("\nNote: every window of this replay file has the same truth, so the table of results will say little. Try another place with  --start N")

    if facts["largest_window_lines"] > BIG_WINDOW:
        say(f"\nNote: one window holds {facts['largest_window_lines']} lines. The processor can score it (processor.yaml says how much memory that needs), but it takes a few seconds.")

    different = len(rows) - agree

    if different and different <= 0.02 * len(rows):
        say(f"\nNote: for {different} lines Drain's template differs from the one in messages.parquet. That is expected for a few lines: Drain's tree "
            "grows while it reads the log, and the processor uses the finished tree (like the NLP demo). Such a line can differ from the table in the verdict of LogBERT.")
    elif different:
        say(f"\nWARNING: for {different} of {len(rows)} lines ({100 * different / len(rows):.1f}%) Drain's template differs from the one in messages.parquet. A few percent "
            "can happen, more than that means that drain.pkl and messages.parquet may not come from the same run of  make preprocess . "
            "Look at the NLP folder: both files must have been written together. Do not go on until you know which it is.")

    say("\nNext: make local-replay   (the same detectors on this file, without a cluster)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
