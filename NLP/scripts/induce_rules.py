"""Induce anomaly-detection rules from labeled training templates (Approach B, step 1).

Usage:
    export OPENAI_API_KEY=...        # the generator defaults to gpt-4o-mini
    python scripts/induce_rules.py

Writes to <output_dir>/rules/:
    rules.json        kept rules in rank order, with their training statistics
    rules.md          the same rules as a Markdown table (for the report)
    candidates.json   every proposed rule, kept or not, with its statistics
Every prompt and response is logged in the LLM cache (llm.cache_dir).
"""

from __future__ import annotations

import argparse
from pathlib import Path

from logad.config import add_config_arguments, config_from_args
from logad.data.processed import load_templates
from logad.llm.client import create_client
from logad.llm.prompts import load_prompt
from logad.rules.induction import induce_rules, make_batches, training_pool, verify_rules
from logad.rules.rule import LABEL_NAMES, rules_markdown, save_rules
from logad.utils import ensure_dir, save_json, set_seed, setup_logging


# Parse command-line arguments for this script.
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    add_config_arguments(parser)
    return parser.parse_args()


# Print a short progress line for each induction batch.
def report_progress(done: int, total: int) -> None:
    print(f"  induction prompt {done}/{total}", end="\r" if done < total else "\n", flush=True)


# Entry point: build the pool, induce, verify, rank and save rules.
def main() -> None:
    setup_logging()
    config = config_from_args(parse_args())
    set_seed(config.seed)
    out_dir = ensure_dir(Path(config.paths.output_dir) / "rules")

    pool = training_pool(load_templates(config.paths.processed_dir))
    n_abnormal = sum(e.label for e in pool)
    print(
        f"Training pool: {len(pool)} templates ({n_abnormal} abnormal, {len(pool) - n_abnormal} normal)"
    )

    batches = make_batches(pool, config.rules.batch_size, config.rules.rounds, seed=config.seed)
    client = create_client(config.llm.generator, config.llm.cache_dir)
    print(f"Prompting {client.name} with {len(batches)} batches...")
    candidates = induce_rules(
        client,
        load_prompt("rule_induction"),
        batches,
        config.rules.max_rules_per_batch,
        progress=report_progress,
    )

    kept, scored = verify_rules(
        candidates, pool, config.rules.min_precision, config.rules.min_support
    )
    save_rules(kept, out_dir / "rules.json")
    save_rules(scored, out_dir / "candidates.json")
    (out_dir / "rules.md").write_text(rules_markdown(kept) + "\n", encoding="utf-8")
    save_json(
        {
            "generator": client.name,
            "n_batches": len(batches),
            "n_candidates": len(candidates),
            "n_kept": len(kept),
            "cache_hits": client.hits,
            "cache_misses": client.misses,
        },
        out_dir / "induction_summary.json",
    )

    print(f"{len(candidates)} unique candidate rules -> {len(kept)} kept after verification")
    for rank, rule in enumerate(kept[:10], start=1):
        print(
            f"  {rank:>2}. [{LABEL_NAMES[rule.label]}] {rule.keywords} ({rule.correct} templates)"
        )
    print(f"Saved rules to {out_dir}")


if __name__ == "__main__":
    main()
