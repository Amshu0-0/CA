"""Run Approach B's conditions on the shared windows (Approach B, step 2).

Usage:
    python scripts/run_rule_guided.py                                  # all four conditions
    python scripts/run_rule_guided.py --conditions keyword             # no LLM needed
    python scripts/run_rule_guided.py --conditions zero_shot few_shot  # a subset

Conditions:
    zero_shot   LLM, template only
    few_shot    LLM + labeled training examples (case-based)
    rule_based  LLM + the induced rules (run scripts/induce_rules.py first)
    keyword     the induced rules as plain string matching, no LLM

For each condition <method> this writes, under the output directory:
    predictions/<method>.parquet             test-window predictions
    results/<method>.json                    window + message metrics, timing, parse failures
    rule_guided/<method>_decisions.csv       one row per template: label, cited rule, reason
"""

from __future__ import annotations

import argparse
from pathlib import Path

from logad.config import ExperimentConfig, add_config_arguments, config_from_args
from logad.data.processed import ProcessedLogs, load_processed
from logad.evaluation.predictions import save_method_result, save_window_predictions
from logad.llm.classifier import LLMTemplateClassifier, select_few_shot_examples
from logad.llm.client import CachedClient, create_client
from logad.rules.deduction import CONDITION_METHODS, ConditionResult, decisions_table, run_condition
from logad.rules.induction import training_pool
from logad.rules.keyword import KeywordClassifier
from logad.rules.rule import Rule, load_rules
from logad.utils import ensure_dir, set_seed, setup_logging


# Parse command-line arguments for this script.
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    add_config_arguments(parser)
    parser.add_argument(
        "--conditions",
        nargs="+",
        choices=sorted(CONDITION_METHODS),
        help="Conditions to run (default: deduction.conditions from the config)",
    )
    return parser.parse_args()


# Load the top-k ranked rules produced by scripts/induce_rules.py.
def load_ranked_rules(config: ExperimentConfig) -> list[Rule]:
    path = Path(config.paths.output_dir) / "rules" / "rules.json"
    if not path.exists():
        raise FileNotFoundError(f"{path} not found. Run `python scripts/induce_rules.py` first.")
    rules = load_rules(path)[: config.rules.top_k]
    if not rules:
        raise ValueError(f"{path} contains no rules; relax rules.min_precision or add rounds")
    return rules


# Build the classifier for one condition. The LLM client is created lazily
# and shared, so the keyword condition never needs an LLM or API key.
def build_classifier(condition: str, config: ExperimentConfig, logs: ProcessedLogs, state: dict):
    if condition == "keyword":
        return KeywordClassifier(load_ranked_rules(config))
    if "client" not in state:
        state["client"] = create_client(config.llm.classifier, config.llm.cache_dir)
    client: CachedClient = state["client"]
    if condition == "few_shot":
        examples = select_few_shot_examples(
            training_pool(logs.templates), config.deduction.few_shot_examples, seed=config.seed
        )
        return LLMTemplateClassifier(client, "few_shot", examples=examples)
    if condition == "rule_based":
        return LLMTemplateClassifier(client, "rule_based", rules=load_ranked_rules(config))
    return LLMTemplateClassifier(client, "zero_shot")


# Save predictions, metrics and the per-template decision table of a condition.
def save_condition(
    result: ConditionResult, condition: str, config: ExperimentConfig, logs: ProcessedLogs, state
) -> None:
    output_dir = Path(config.paths.output_dir)
    if "test" in result.evaluations:
        test = result.evaluations["test"]
        save_window_predictions(
            output_dir,
            result.method,
            logs.windows_in("test")["window_id"].to_numpy(),
            test["window_preds"],
            scores=test["window_flagged_counts"],
        )

    summary = {
        "approach": "B",
        "condition": condition,
        "classifier": "keyword matching" if condition == "keyword" else state["client"].name,
        "settings": {
            "n_rules": config.rules.top_k if condition in ("rule_based", "keyword") else None,
            "few_shot_examples": (
                config.deduction.few_shot_examples if condition == "few_shot" else None
            ),
        },
        "n_templates_classified": len(result.decisions),
        "n_unparsed_answers": result.n_unparsed,
        "n_cached_answers": result.n_cached,
    }
    for split, evaluation in result.evaluations.items():
        name = "validation" if split == "val" else split
        summary[name] = evaluation["window"]
        summary[f"{name}_message_level"] = evaluation["message"]
    summary["inference_seconds"] = result.inference_seconds.get("test")
    summary["inference_seconds_by_split"] = result.inference_seconds
    save_method_result(output_dir, result.method, summary)

    table_dir = ensure_dir(output_dir / "rule_guided")
    decisions_table(result, logs.templates).to_csv(
        table_dir / f"{result.method}_decisions.csv", index=False
    )


# Print one progress line while templates are being classified.
def report_progress(done: int, total: int) -> None:
    print(f"  classified {done}/{total} templates", end="\r" if done < total else "\n", flush=True)


# Entry point: run every requested condition and print its test metrics.
def main() -> None:
    setup_logging()
    args = parse_args()
    config = config_from_args(args)
    set_seed(config.seed)
    conditions = args.conditions or config.deduction.conditions
    logs = load_processed(
        config.paths.processed_dir, message_columns=["template_id", "label", "split"]
    )

    state: dict = {}
    for condition in conditions:
        method = CONDITION_METHODS[condition]
        print(f"\n== {method} ==")
        classifier = build_classifier(condition, config, logs, state)
        result = run_condition(logs, classifier, method, config.deduction.splits, report_progress)
        save_condition(result, condition, config, logs, state)

        for split, evaluation in result.evaluations.items():
            w, m = evaluation["window"], evaluation["message"]
            print(
                f"  {split:<5} windows P={w['precision']:.3f} R={w['recall']:.3f} "
                f"F1={w['f1']:.3f} | messages F1={m['f1']:.3f}"
            )
        print(
            f"  {len(result.decisions)} templates, {result.n_unparsed} unparsed answers, "
            f"test inference {result.inference_seconds.get('test', 0.0):.1f}s"
        )


if __name__ == "__main__":
    main()
