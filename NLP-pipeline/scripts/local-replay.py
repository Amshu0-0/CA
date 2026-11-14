# Runs the pipeline's detectors on the replay file on THIS computer, with no cluster, no Kafka and no MongoDB.
#
#   make local-replay
#   make local-replay LOCAL_ARGS="--limit 5000"            only the first 5000 lines (a quick try)
#   python3 NLP-pipeline/scripts/local-replay.py --save NLP-pipeline/evidence/local-replay.json
#
# It is the same program as in the cluster (processor/engine.py): the same Drain parser, keyword rules, saved LLM decisions, windows and
# LogBERT, fed with the lines of bundle/bgl-replay.log in batches, like Kafka would hand them to the processor. Only the two ends differ:
# the lines come from the file, and the documents go into dictionaries instead of MongoDB. So the numbers printed here are the ones that
# "make results" must print for the cluster (make results --compare evidence/local-replay.json checks that).
#
# It also checks the engine against your NLP code: the windows that the engine hands out must be exactly the windows that the NLP project's
# own function (logad.data.windows.sliding_windows) builds from the same file, with the same numbers of anomalous windows (replay-info.json).
# Run it in the Python environment of your NLP project.

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PIPELINE = HERE.parent
REPO = PIPELINE.parent

for folder in (REPO / "NLP" / "src", PIPELINE / "processor", PIPELINE / "restapi", HERE):
    sys.path.insert(0, str(folder))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run the detectors of the pipeline on the replay file, without a cluster.")
    parser.add_argument("--bundle", default=str(PIPELINE / "bundle"), help="the folder made by export-nlp-bundle.py (default: NLP-pipeline/bundle)")
    parser.add_argument("--file", help="the replay file (default: <bundle>/bgl-replay.log)")
    parser.add_argument("--limit", type=int, help="replay only the first N lines")
    parser.add_argument("--batch", type=int, default=500, help="lines per batch, like the processor's (default 500)")
    parser.add_argument("--run", default="local", help="the name of the replay (default: local)")
    parser.add_argument("--save", metavar="FILE", help="write the summary to this JSON file (make results --compare reads it)")
    args = parser.parse_args(argv)

    from engine import BundleError, Detectors, Engine, Record
    from sink import MemorySink
    from summary import summarize
    from tables import describe, differences, reference

    bundle = Path(args.bundle)
    replay = Path(args.file) if args.file else bundle / "bgl-replay.log"

    if not replay.is_file():
        print(f"Not done: {replay} does not exist. Make it with:  make export-bundle", file=sys.stderr)
        return 1

    started = time.time()

    try:
        detectors = Detectors.load(str(bundle))
    except BundleError as error:
        print(f"Not done: {error}", file=sys.stderr)
        return 1

    print(f"The models are loaded ({time.time() - started:.1f} s): {len(detectors.extractor.templates)} templates, LogBERT with {len(detectors.logbert.vocab) - 4} keys, "
          f"{len(detectors.keyword.rules)} keyword rules, {len(detectors.llm)} saved LLM decisions")

    lines = [line.rstrip("\r\n") for line in replay.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()]

    if args.limit:
        lines = lines[: args.limit]

    # The messages, as the producer sends them: the lines, then the end marker.
    messages = [{"kind": "log", "run": args.run, "seq": i, "line": line} for i, line in enumerate(lines)]
    messages.append({"kind": "end", "run": args.run, "lines": len(lines)})
    records = [Record("system-logs", 0, offset, 0, value) for offset, value in enumerate(messages)]

    sink = MemorySink()
    engine = Engine(detectors, sink.load_state)
    counts, last_report, begun = {}, time.time(), time.time()

    for first in range(0, len(records), args.batch):
        out = engine.process(records[first: first + args.batch])
        sink.write(out)

        for name, value in out.counts.items():
            counts[name] = counts.get(name, 0) + value

        if time.time() - last_report > 10:
            print(f"  {min(first + args.batch, len(lines))} of {len(lines)} lines, {len(sink.windows)} windows scored so far ({time.time() - begun:.0f} s)")
            last_report = time.time()

    seconds = time.time() - begun
    windows = list(sink.windows.values())
    events = list(sink.events.values())
    run = dict(sink.runs[args.run])
    run["run"] = args.run

    summary = {
        "run": {key: value for key, value in run.items() if key not in ("origin", "next_k", "last_key", "done", "_id")},
        "lines": {
            "stored": len(events),
            "with_alert_tag": sum(e["alert"] for e in events),
            "template_not_known": sum(1 for e in events if e["template_id"] == -1),
            "llm_has_no_saved_decision": sum(1 for e in events if e["llm"] is None),
        },
        **summarize(windows),
    }

    print(f"\nReplayed {len(lines)} lines in {seconds:.0f} s ({len(lines) / max(seconds, 1e-9):.0f} lines per second, {len(windows)} windows scored on this computer)\n")

    for text in describe(summary):
        print(text)

    for text in reference(REPO / "NLP" / "results" / "comparison.csv"):
        print(("\n" if text.startswith("For comparison") else "") + text)

    problems = []

    if counts.get("late") or counts.get("bad"):
        problems.append(f"{counts.get('late', 0)} lines were late and {counts.get('bad', 0)} were not BGL lines: a replay file made by the export script has neither")

    if args.limit is None:
        info_path = bundle / "replay-info.json"

        if info_path.is_file():
            info = json.loads(info_path.read_text(encoding="utf-8"))

            for key, got in (("lines", summary["lines"]["stored"]), ("alert_lines", summary["lines"]["with_alert_tag"]), ("windows", summary["windows"]),
                             ("anomalous_windows", summary["anomalous_windows"])):
                if info[key] != got:
                    problems.append(f"replay-info.json says {key} = {info[key]}, the engine found {got}")

            if not problems:
                print(f"\nThe engine built exactly the windows that the NLP code expects from this file: {info['windows']} windows, {info['anomalous_windows']} anomalous, "
                      f"{info['alert_lines']} alert lines, the largest window {info['largest_window_lines']} lines.")
        else:
            print("\n(replay-info.json is missing, so the windows were not compared with the NLP code's. Run:  make export-bundle)")

    if args.save:
        target = Path(args.save)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        print(f"\nThe summary is saved in {target}. After  make replay  the cluster's numbers can be compared with it:  make results RESULTS_ARGS=\"--compare {target}\"")

    if problems:
        print("\nNot as expected:", file=sys.stderr)

        for problem in problems:
            print("  - " + problem, file=sys.stderr)

        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
