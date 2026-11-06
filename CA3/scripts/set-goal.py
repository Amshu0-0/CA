# Shows the goals of the pipeline (slo.json) and what the last scaling test measured for each of them, and changes the number or the reason of one goal.
#
#   make goals                                              list the goals, each with the verdict of the last scaling test
#   make goals GOAL_ARGS="scale-up 120"                     the goal "scale-up" becomes 120 (in the unit shown in the list)
#   make goals GOAL_ARGS="scale-up 120 --why 'one sentence'"   also write down why
#
# A goal is a number that a test can measure, for example "at most 180 seconds until all producer pods are ready". The goals live in
# slo.json (one folder up). The saved scaling test (evidence/step6-scale-*) is judged against the goals as they are now, every time you run
# this, so you see at once what a change does: the measurements stay the same, only the verdicts can change. "make scale-report" writes the
# table and the chart again from the same files. Nothing here touches the cluster.

import argparse
import json
import sys
from pathlib import Path

FOLDER = Path(__file__).resolve().parent.parent
FILE = FOLDER / "slo.json"

parser = argparse.ArgumentParser(description="List the goals, or change one of them.")
parser.add_argument("goal", nargs="?", help="the id of the goal to change (leave out to list all goals)")
parser.add_argument("number", nargs="?", help="the new number")
parser.add_argument("--why", help="a new reason (one or two sentences)")
args = parser.parse_args()

try:
    data = json.loads(FILE.read_text())
    goals = data["objectives"]
except (OSError, ValueError, KeyError) as problem:
    sys.exit("Cannot read " + str(FILE) + ": " + str(problem))


def say(g):
    return ("at least " if g["direction"] == "at least" else "at most ") + format(g["goal"], ",") + " " + g["unit"]


# What the last scaling test measured, judged against the goals as they are now: ({goal id: its verdict}, a sentence when there is nothing to show).
# The numbers come from the files that make scale-test saved; the same functions draw the table of make scale-report.
def last_test():
    try:
        import scalereport

        rows, summary = scalereport.load(FOLDER / "evidence", "step6-scale")
    except FileNotFoundError:
        return {}, "No scaling test is saved yet, so the goals are not judged. Run  make scale-test  first."
    except Exception as problem:
        return {}, "The saved scaling test could not be read (" + str(problem)[:100] + "), so the goals are not judged."

    return {v["id"]: v for v in scalereport.judge(rows, summary)}, None


def verdict_line(v):
    return "last test: " + v["measured"] + "  ->  " + v["verdict"]


if args.goal is None:
    judged, note = last_test()
    print("The goals in slo.json:\n")

    for g in goals:
        print("  " + g["id"].ljust(16) + say(g))
        print("  " + " " * 16 + g["text"])
        print("  " + " " * 16 + "why: " + g["why"])

        if g["id"] in judged:
            print("  " + " " * 16 + verdict_line(judged[g["id"]]))

        print()

    if note:
        print(note + "\n")
    else:
        tally = [v["verdict"] for v in judged.values()]
        print("Last test: " + str(tally.count("met")) + " met, " + str(tally.count("NOT met")) + " NOT met, " + str(tally.count("not measured")) + " not measured.\n")

    print("Change one:  make goals GOAL_ARGS=\"<id> <number>\"      Write the table and the chart again:  make scale-report")
    sys.exit(0)

found = [g for g in goals if g["id"] == args.goal]

if not found:
    sys.exit("There is no goal called '" + args.goal + "'. The goals are: " + ", ".join(g["id"] for g in goals))

goal = found[0]

if args.number is None and args.why is None:
    sys.exit("Say the new number, for example:  make goals GOAL_ARGS=\"" + goal["id"] + " " + format(goal["goal"], "g") + "\"")

before = say(goal)

if args.number is not None:
    try:
        number = float(args.number.replace(",", ""))
    except ValueError:
        sys.exit("'" + args.number + "' is not a number.")

    if not number > 0 or number != number or number in (float("inf"),):
        sys.exit("The number must be above 0.")

    goal["goal"] = int(number) if number == int(number) else number

if args.why is not None:
    if len(args.why.strip()) < 20:
        sys.exit("Write the reason as a sentence (at least 20 characters): why is this the right number?")

    goal["why"] = args.why.strip()

FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
print(goal["id"] + ":  " + before + "  ->  " + say(goal))

if args.why is not None:
    print("why: " + goal["why"])

judged, note = last_test()

if goal["id"] in judged:
    print(verdict_line(judged[goal["id"]]))
elif note:
    print(note)

print("Saved in slo.json. Write the table and the chart again with:  make scale-report")
