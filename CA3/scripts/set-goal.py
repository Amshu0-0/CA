# Shows the goals of the pipeline (slo.json) and what the last scaling test and the last resilience drill measured for each of them, and changes the number or the reason of one goal.
#
#   make goals                                                   list the goals, each with the verdict of the last test that measures it
#   make goals GOAL_ARGS="scale-up 120 --why 'one sentence'"     the goal "scale-up" becomes 120 (in the unit shown in the list), and the reason says why
#   make goals GOAL_ARGS="scale-up --why 'one sentence'"         only the reason changes
#
# A new number always comes with its reason: the reason that is in slo.json was written for the old number, so it would be wrong for the new one.
# A goal is a number that a test can measure, for example "at most 180 seconds until all producer pods are ready". The goals live in
# slo.json (one folder up). The saved scaling test (evidence/step6-scale-*) and the saved drill (evidence/step7-drill-*) are judged against the goals
# as they are now, every time you run this, so you see at once what a change does: the measurements stay the same, only the verdicts can change.
# "make scale-report" and "make drill-report" write the tables and the charts again from the same files. Nothing here touches the cluster.

import argparse
import json
import sys
from pathlib import Path

FOLDER = Path(__file__).resolve().parent.parent
FILE = FOLDER / "slo.json"

parser = argparse.ArgumentParser(description="List the goals, or change one of them.")
parser.add_argument("goal", nargs="?", help="the id of the goal to change (leave out to list all goals)")
parser.add_argument("number", nargs="?", help="the new number")
parser.add_argument("--why", help="the reason (one or two sentences); a new number needs one")
args = parser.parse_args()

try:
    data = json.loads(FILE.read_text())
    goals = data["objectives"]
except (OSError, ValueError, KeyError) as problem:
    sys.exit("Cannot read " + str(FILE) + ": " + str(problem))


def say(g):
    return ("at least " if g["direction"] == "at least" else "at most ") + format(g["goal"], ",") + " " + g["unit"]


# What the last scaling test and the last drill measured, judged against the goals as they are now: ({goal id: its verdict}, [a sentence for each test that is not there]).
# The numbers come from the files that make scale-test and make drill saved; the same functions draw the tables of make scale-report and make drill-report.
def last_test():
    verdicts, notes = {}, []

    try:
        import scalereport

        rows, summary = scalereport.load(FOLDER / "evidence", "step6-scale")
        verdicts.update({v["id"]: v for v in scalereport.judge(rows, summary)})
    except FileNotFoundError:
        notes.append("No scaling test is saved yet, so the scaling goals are not judged. Run  make scale-test  first.")
    except Exception as problem:
        notes.append("The saved scaling test could not be read (" + str(problem)[:100] + "), so the scaling goals are not judged.")

    try:
        import drillreport

        verdicts.update({v["id"]: v for v in drillreport.judge(drillreport.load(FOLDER / "evidence", "step7-drill"))})
    except FileNotFoundError:
        notes.append("No resilience drill of all five tiers is saved yet, so the drill goals are not judged. Run  make drill  first.")
    except Exception as problem:
        notes.append("The saved drill could not be read (" + str(problem)[:100] + "), so the drill goals are not judged.")

    return verdicts, notes


def verdict_line(v):
    return "last test: " + v["measured"] + "  ->  " + v["verdict"]


# The command that draws the table of the test that measures this goal again.
def report_command(g):
    return "make drill-report" if g.get("test") == "drill" else "make scale-report"


if args.goal is None:
    judged, notes = last_test()
    print("The goals in slo.json:\n")

    for g in goals:
        print("  " + g["id"].ljust(16) + say(g))
        print("  " + " " * 16 + g["text"])
        print("  " + " " * 16 + "why: " + g["why"])

        if g["id"] in judged:
            print("  " + " " * 16 + verdict_line(judged[g["id"]]))

        print()

    for note in notes:
        print(note + "\n")

    if judged:
        tally = [v["verdict"] for v in judged.values()]
        print("Last tests: " + str(tally.count("met")) + " met, " + str(tally.count("NOT met")) + " NOT met, " + str(tally.count("not measured")) + " not measured.\n")

    print("Change one:  make goals GOAL_ARGS=\"<id> <number> --why 'the reason'\"      Write the table and the chart again:  make scale-report  or  make drill-report")
    sys.exit(0)

found = [g for g in goals if g["id"] == args.goal]

if not found:
    sys.exit("There is no goal called '" + args.goal + "'. The goals are: " + ", ".join(g["id"] for g in goals))

goal = found[0]

if args.number is None and args.why is None:
    sys.exit("Say the new number and the reason, for example:  make goals GOAL_ARGS=\"" + goal["id"] + " <new number> --why 'one sentence: why is this the right number'\"")

before = say(goal)
old_number, old_reason = goal["goal"], goal["why"]

if args.number is not None:
    try:
        number = float(args.number.replace(",", ""))
    except ValueError:
        sys.exit("'" + args.number + "' is not a number.")

    if not number > 0 or number != number or number in (float("inf"),):
        sys.exit("The number must be above 0.")

    new_number = int(number) if number == int(number) else number

    # The reason in slo.json was written for the old number. A new number with the old reason would say something that is not true.
    if new_number != old_number and args.why is None:
        sys.exit("A new number needs its reason: the reason that is in slo.json now was written for the old number.\n"
                 "For example:  make goals GOAL_ARGS=\"" + goal["id"] + " " + str(new_number) + " --why 'one sentence: why is this the right number'\"")

    goal["goal"] = new_number

if args.why is not None:
    new_reason = args.why.strip()

    if len(new_reason) < 20:
        sys.exit("Write the reason as a sentence (at least 20 characters): why is this the right number?")

    if goal["goal"] != old_number and new_reason == old_reason:
        sys.exit("That is the reason that is there already, and it was written for the old number. Write why the NEW number is the right one.")

    goal["why"] = new_reason

FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
print(goal["id"] + ":  " + before + "  ->  " + say(goal))

if args.why is not None:
    print("why: " + goal["why"])

judged, notes = last_test()

if goal["id"] in judged:
    print(verdict_line(judged[goal["id"]]))
else:
    for note in notes:
        print(note)

print("Saved in slo.json. Write the table and the chart again with:  " + report_command(goal))
