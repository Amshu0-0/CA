"""Error analysis of every evaluated method on the test windows.

Usage:
    python scripts/analyze.py
    python scripts/analyze.py --config configs/bgl_2k.yaml

Writes analysis/analysis.md (tables for the report) and one CSV per table to
the output directory.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from logad.config import add_config_arguments, config_from_args
from logad.data.processed import load_processed
from logad.evaluation.analysis import (
    combination_metrics,
    exclusive_alert_templates,
    keyword_rule_sensitivity,
    label_drift,
    method_agreement,
    missed_alert_templates,
    per_category_recall,
    rule_faithfulness,
    template_errors,
    template_purity,
)
from logad.evaluation.compare import METHOD_LABELS, align_predictions, available_methods
from logad.evaluation.predictions import load_window_predictions
from logad.rules.rule import Rule, load_rules
from logad.utils import ensure_dir, save_json, setup_logging

# Approach A's methods; every other method with predictions belongs to Approach B.
APPROACH_A = ("logbert", "logbert_full")


# Parse command-line arguments for this script.
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    add_config_arguments(parser)
    return parser.parse_args()


# Render a DataFrame as a Markdown table (floats with 3 decimals).
def to_markdown(frame: pd.DataFrame) -> str:
    if frame.empty:
        return "_(none)_"
    header = "| " + " | ".join(map(str, frame.columns)) + " |"
    divider = "|" + "---|" * len(frame.columns)
    rows = []
    for values in frame.itertuples(index=False):
        cells = [f"{v:.3f}" if isinstance(v, float) else str(v).replace("|", "\\|") for v in values]
        rows.append("| " + " | ".join(cells) + " |")
    return "\n".join([header, divider, *rows])


# Check the rule-based LLM's citations against `rules` (the ranked rules its
# prompt showed), if that condition was run. Writes rule_faithfulness.json and
# .csv and returns the Markdown section.
def rule_sections(output_dir: Path, out: Path, rules: list[Rule]) -> list[str]:
    path = output_dir / "rule_guided" / "llm_rule_based_decisions.csv"
    if not path.exists():
        return []
    summary, table = rule_faithfulness(pd.read_csv(path, keep_default_na=False), rules)
    if table.empty:
        return []
    save_json(summary, out / "rule_faithfulness.json")
    table.to_csv(out / "rule_faithfulness.csv", index=False)

    # What the LLM adds to the rules: templates it flags although no keyword rule does.
    extra = table[(table["label"] == 1) & (table["keyword_label"] == 0)]
    extra = extra.sort_values("n_messages", ascending=False)
    extra = extra.assign(cited_rule=extra["cited_rule"].astype("string").fillna("-"))
    columns = [
        "template_id",
        "template",
        "has_alert",
        "n_messages",
        "cited_rule",
        "cited_rule_occurs",
        "reason",
    ]
    return [
        "## Does the rule-based LLM apply the rules it cites?\n",
        "\n".join(f"- {name.replace('_', ' ')}: {value}" for name, value in summary.items()) + "\n",
        "Templates the LLM labels abnormal although no keyword rule does:\n",
        to_markdown(extra[columns]),
        "",
    ]


# Entry point: run every analysis and write the Markdown summary.
def main() -> None:
    setup_logging()
    config = config_from_args(parse_args())
    output_dir = Path(config.paths.output_dir)
    out = ensure_dir(output_dir / "analysis")
    logs = load_processed(
        config.paths.processed_dir,
        message_columns=["template_id", "label", "split", "alert_tag"],
    )
    test_windows = logs.windows_in("test")

    predictions = {
        method: align_predictions(
            load_window_predictions(output_dir, method), test_windows, method
        )["pred"].to_numpy()
        for method in available_methods(output_dir)
    }

    sections = ["# Error analysis (test period)\n"]

    # 1. Template label purity and the template-level ceiling.
    purity, mixed = template_purity(logs, "test")
    save_json(purity, out / "template_purity.json")
    mixed.to_csv(out / "mixed_templates.csv", index=False)
    oracle_msg, oracle_win = purity["oracle_message_level"], purity["oracle_window_level"]
    sections += [
        "## Template label purity\n",
        f"{purity['n_mixed_templates']} of {purity['n_templates']} test templates are mixed "
        f"(hold both normal and alert messages); they contain "
        f"{purity['messages_in_mixed_templates']:,} of {purity['n_messages']:,} test messages and "
        f"{purity['anomalous_messages_in_mixed_templates']:,} of "
        f"{purity['anomalous_messages']:,} alert messages.\n",
        "Best possible template-level classifier (each template labeled by its test majority): "
        f"message-level F1 = {oracle_msg['f1']:.3f}, window-level F1 = {oracle_win['f1']:.3f}.\n",
        to_markdown(mixed.head(15)),
        "",
    ]

    # 1b. Templates whose label changes between the training and test periods.
    drift = label_drift(logs.templates, "test")
    drift.to_csv(out / "label_drift.csv", index=False)
    sections += [
        "## Templates whose label changes between training and test\n",
        "The majority label in the training period differs from the one in the test period. "
        "Methods that learn template labels from training data cannot get these right.\n",
        to_markdown(drift.head(15)),
        "",
    ]

    if predictions:
        # 2. Recall per alert category.
        categories = per_category_recall(logs, predictions, "test")
        categories.to_csv(out / "per_category_recall.csv", index=False)
        sections += ["## Recall per alert category (window level)\n", to_markdown(categories), ""]

        # 3. Agreement between methods.
        agreement = method_agreement(test_windows["label"].to_numpy(), predictions)
        agreement.to_csv(out / "method_agreement.csv", index=False)
        sections += ["## Agreement on anomalous windows\n", to_markdown(agreement), ""]

        # 3b. Would combining the approaches help, and what does each add?
        a_methods = [m for m in predictions if m in APPROACH_A]
        b_methods = [m for m in predictions if m not in APPROACH_A]
        pairs = [(a, b) for a in a_methods for b in b_methods]
        if pairs:
            combos = combination_metrics(test_windows["label"].to_numpy(), predictions, pairs)
            combos.to_csv(out / "combinations.csv", index=False)
            sections += [
                "## Combining Approach A and Approach B\n",
                "A window is flagged if either method flags it (OR) or only if both do (AND).\n",
                to_markdown(combos),
                "",
            ]
        if "logbert" in predictions and b_methods:
            sections.append("## Anomalous windows only one approach catches\n")
            for method in b_methods:
                table = exclusive_alert_templates(
                    logs, predictions["logbert"], predictions[method], ("LogBERT", method)
                )
                table.to_csv(out / f"exclusive_logbert_vs_{method}.csv", index=False)
                title = f"### A LogBERT vs {METHOD_LABELS.get(method, method)}\n"
                sections += [title, to_markdown(table), ""]

        # 4. What each method misses.
        sections.append("## Alert templates in missed windows\n")
        for method, preds in predictions.items():
            missed = missed_alert_templates(logs, preds, "test")
            missed.to_csv(out / f"missed_alerts_{method}.csv", index=False)
            sections += [f"### {METHOD_LABELS.get(method, method)}\n", to_markdown(missed), ""]

    # 5. Approach B's misclassified templates.
    decision_files = sorted((output_dir / "rule_guided").glob("*_decisions.csv"))
    if decision_files:
        sections.append("## Misclassified templates (Approach B)\n")
    for path in decision_files:
        method = path.stem.removesuffix("_decisions")
        errors = template_errors(pd.read_csv(path, keep_default_na=False))
        errors.to_csv(out / f"template_errors_{method}.csv", index=False)
        sections += [f"### {METHOD_LABELS.get(method, method)}\n", to_markdown(errors.head(15)), ""]

    # 6. How the induced rules are used: by the rule-based LLM, and by the
    #    keyword baseline as a function of how many rules it gets.
    rules_path = output_dir / "rules" / "rules.json"
    if rules_path.exists():
        rules = load_rules(rules_path)
        sections += rule_sections(output_dir, out, rules[: config.rules.top_k])
        splits = [split for split in ("val", "test") if not logs.windows_in(split).empty]
        ks = sorted({*range(5, 55, 5), 60, 80, len(rules), config.rules.top_k})
        sensitivity = keyword_rule_sensitivity(logs, rules, ks, splits)
        sensitivity.to_csv(out / "keyword_rule_sensitivity.csv", index=False)
        sections += [
            "## Keyword rules: sensitivity to the number of rules\n",
            f"The reported runs use the top {config.rules.top_k} of {len(rules)} verified rules. "
            "Choose k on the validation columns; the test columns are for reporting only.\n",
            to_markdown(sensitivity),
            "",
        ]

    (out / "analysis.md").write_text("\n".join(sections) + "\n", encoding="utf-8")
    print(f"Wrote {out / 'analysis.md'}")


if __name__ == "__main__":
    main()
