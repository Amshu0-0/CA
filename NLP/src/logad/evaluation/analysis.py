"""Error analysis for the report.

* ``template_purity``        how often messages sharing a template carry
                             different labels, and the best F1 any
                             template-level method could reach (an oracle that
                             labels each template by its test majority)
* ``label_drift``            templates whose majority label changes between
                             the training period and the test period
* ``per_category_recall``    recall of every method per BGL alert category
                             (KERNDTLB, APPREAD, ...)
* ``method_agreement``       which anomalous windows each pair of methods
                             catches together / alone / not at all
* ``missed_alert_templates`` the alert templates most often inside the
                             windows a method missed
* ``template_errors``        Approach B's misclassified templates

Comparing the two approaches and explaining Approach B:

* ``combination_metrics``       what flagging a window when either (OR) or both
                                (AND) of two methods flag it would score
* ``exclusive_alert_templates`` the alert templates in the anomalous windows
                                that only one of two methods catches
* ``rule_faithfulness``         whether the rule the rule-based LLM cites
                                actually occurs in the template it classified
* ``keyword_rule_sensitivity``  the keyword baseline's scores as a function of
                                how many ranked rules it uses (the top-k cut-off)
"""

from __future__ import annotations

from itertools import combinations
from typing import Any

import numpy as np
import pandas as pd

from logad.data.processed import ProcessedLogs
from logad.data.windows import count_in_windows
from logad.evaluation.metrics import binary_metrics, windows_from_message_flags
from logad.evaluation.template_level import evaluate_template_labels, templates_in_splits
from logad.rules.keyword import KeywordClassifier
from logad.rules.rule import Rule


# Label purity of templates within one split, plus the template-level oracle.
#
# A template is "mixed" in the split if some, but not all, of its messages
# there are alerts. No method that assigns one label per template can be
# right about every message of a mixed template; the oracle (majority label
# per template) shows the resulting ceiling at message and window level.
def template_purity(
    logs: ProcessedLogs, split: str = "test"
) -> tuple[dict[str, Any], pd.DataFrame]:
    templates = logs.templates
    total, anomalous = templates[f"n_{split}"], templates[f"n_{split}_anomalous"]
    present = total > 0
    mixed = present & (anomalous > 0) & (anomalous < total)

    oracle = {
        int(t): int(2 * a > n)
        for t, a, n in zip(templates["template_id"], anomalous, total, strict=True)
        if n > 0
    }
    abnormal_ids = np.array([t for t, label in oracle.items() if label], dtype=np.int64)
    flags = np.isin(logs.template_ids, abnormal_ids).astype(np.int8)
    in_split = (logs.messages["split"].astype(str) == split).to_numpy()
    windows = logs.windows_in(split)
    oracle_windows = windows_from_message_flags(flags, windows)

    summary = {
        "split": split,
        "n_templates": int(present.sum()),
        "n_mixed_templates": int(mixed.sum()),
        "n_messages": int(total.sum()),
        "messages_in_mixed_templates": int(total[mixed].sum()),
        "anomalous_messages": int(anomalous.sum()),
        "anomalous_messages_in_mixed_templates": int(anomalous[mixed].sum()),
        "oracle_message_level": binary_metrics(
            logs.message_labels[in_split], flags[in_split]
        ).as_dict(),
        "oracle_window_level": binary_metrics(
            windows["label"].to_numpy(), oracle_windows
        ).as_dict(),
    }
    columns = ["template_id", "template", f"n_{split}", f"n_{split}_anomalous", "alert_tags"]
    mixed_table = templates.loc[mixed, columns].sort_values(f"n_{split}", ascending=False)
    return summary, mixed_table.reset_index(drop=True)


# Templates whose majority label in `split` differs from their majority label
# in the training period. Methods that learn template labels from training
# data (rules, few-shot examples) cannot get these right, and the purity check
# above cannot see them, because each period on its own may be pure.
def label_drift(templates: pd.DataFrame, split: str = "test") -> pd.DataFrame:
    seen = templates[(templates["n_train"] > 0) & (templates[f"n_{split}"] > 0)]
    train_label = 2 * seen["n_train_anomalous"] > seen["n_train"]
    split_label = 2 * seen[f"n_{split}_anomalous"] > seen[f"n_{split}"]
    columns = [
        "template_id",
        "template",
        "n_train",
        "n_train_anomalous",
        f"n_{split}",
        f"n_{split}_anomalous",
    ]
    drifted = seen.loc[train_label != split_label, columns]
    return drifted.sort_values(f"n_{split}", ascending=False).reset_index(drop=True)


# For every alert category, the share of test windows containing it that
# each method flagged. `predictions` maps method -> 0/1 array aligned with
# logs.windows_in(split).
def per_category_recall(
    logs: ProcessedLogs, predictions: dict[str, np.ndarray], split: str = "test"
) -> pd.DataFrame:
    windows = logs.windows_in(split)
    pairs = windows[["start_idx", "end_idx"]].to_numpy()
    tags = logs.messages["alert_tag"].astype(str).to_numpy()
    rows = []
    for category in sorted(set(tags[logs.message_labels == 1])):
        contains = count_in_windows((tags == category).astype(np.int64), pairs) > 0
        if not contains.any():
            continue
        row = {"category": category, "n_windows": int(contains.sum())}
        for method, preds in predictions.items():
            row[method] = float(np.asarray(preds)[contains].mean())
        rows.append(row)
    return pd.DataFrame(rows).sort_values("n_windows", ascending=False).reset_index(drop=True)


# For every pair of methods, split the anomalous windows into caught by both,
# by only one of them, or by neither; also count shared false positives.
def method_agreement(labels: np.ndarray, predictions: dict[str, np.ndarray]) -> pd.DataFrame:
    labels = np.asarray(labels).astype(bool)
    rows = []
    for (name_a, a), (name_b, b) in combinations(predictions.items(), 2):
        a, b = np.asarray(a).astype(bool), np.asarray(b).astype(bool)
        rows.append(
            {
                "method_a": name_a,
                "method_b": name_b,
                "both_caught": int(np.sum(labels & a & b)),
                "only_a": int(np.sum(labels & a & ~b)),
                "only_b": int(np.sum(labels & ~a & b)),
                "neither": int(np.sum(labels & ~a & ~b)),
                "shared_false_positives": int(np.sum(~labels & a & b)),
            }
        )
    return pd.DataFrame(rows)


# The alert templates inside the windows selected by `mask` (a boolean array
# aligned with logs.windows_in(split)), each with the number of selected
# windows it occurs in; the `top` most frequent are returned.
def alert_templates_in_windows(
    logs: ProcessedLogs, mask: np.ndarray, split: str = "test", top: int = 10
) -> pd.DataFrame:
    windows = logs.windows_in(split)
    selected = windows[np.asarray(mask, dtype=bool)]
    counts: dict[int, int] = {}
    for start, end in selected[["start_idx", "end_idx"]].to_numpy():
        alert_mask = logs.message_labels[start:end] == 1
        for template_id in np.unique(logs.template_ids[start:end][alert_mask]):
            counts[int(template_id)] = counts.get(int(template_id), 0) + 1
    rows = [
        {"template_id": t, "template": logs.template_text[t], "windows": n}
        for t, n in sorted(counts.items(), key=lambda item: -item[1])[:top]
    ]
    return pd.DataFrame(rows, columns=["template_id", "template", "windows"])


# The alert templates that occur most often inside the anomalous windows a
# method missed (false negatives), with the number of missed windows each
# appears in. Explains *what kind* of anomaly a method overlooks.
def missed_alert_templates(
    logs: ProcessedLogs, predictions: np.ndarray, split: str = "test", top: int = 10
) -> pd.DataFrame:
    windows = logs.windows_in(split)
    missed = (windows["label"].to_numpy() == 1) & (np.asarray(predictions) == 0)
    table = alert_templates_in_windows(logs, missed, split, top)
    return table.rename(columns={"windows": "missed_windows"})


# What two methods would score together: a window is flagged if either of
# them flags it (OR, which can only raise recall) or only if both do (AND,
# which can only raise precision). One row per pair and combination.
def combination_metrics(
    labels: np.ndarray, predictions: dict[str, np.ndarray], pairs: list[tuple[str, str]]
) -> pd.DataFrame:
    labels = np.asarray(labels).astype(bool)
    rows = []
    for name_a, name_b in pairs:
        a = np.asarray(predictions[name_a]).astype(bool)
        b = np.asarray(predictions[name_b]).astype(bool)
        for combination, preds in (("either (OR)", a | b), ("both (AND)", a & b)):
            metrics = binary_metrics(labels, preds)
            rows.append(
                {
                    "method_a": name_a,
                    "method_b": name_b,
                    "combination": combination,
                    "precision": metrics.precision,
                    "recall": metrics.recall,
                    "f1": metrics.f1,
                    "fp": metrics.fp,
                }
            )
    return pd.DataFrame(rows)


# The alert templates in the anomalous windows caught by only one of two
# methods, or by neither: what each approach contributes that the other
# lacks. `caught_by` reads "only <name_a>", "only <name_b>" or "neither".
def exclusive_alert_templates(
    logs: ProcessedLogs,
    preds_a: np.ndarray,
    preds_b: np.ndarray,
    names: tuple[str, str] = ("a", "b"),
    split: str = "test",
    top: int = 5,
) -> pd.DataFrame:
    anomalous = logs.windows_in(split)["label"].to_numpy() == 1
    a, b = np.asarray(preds_a).astype(bool), np.asarray(preds_b).astype(bool)
    groups = {
        f"only {names[0]}": anomalous & a & ~b,
        f"only {names[1]}": anomalous & ~a & b,
        "neither": anomalous & ~a & ~b,
    }
    frames = []
    for caught_by, mask in groups.items():
        table = alert_templates_in_windows(logs, mask, split, top)
        if not table.empty:
            table.insert(0, "caught_by", caught_by)
            table.insert(1, "group_windows", int(mask.sum()))
            frames.append(table)
    columns = ["caught_by", "group_windows", "template_id", "template", "windows"]
    if not frames:
        return pd.DataFrame(columns=columns)
    return pd.concat(frames, ignore_index=True)[columns]


# The 1-based rule number in a decisions table cell ("3", "3.0", 3.0), or
# None if the cell is empty or outside 1..n_rules.
def _rule_number(value: Any, n_rules: int) -> int | None:
    try:
        number = int(float(value))
    except (TypeError, ValueError):  # empty cell or NaN
        return None
    return number if 1 <= number <= n_rules else None


# Check the rule-based LLM's answers against the rules it cites.
#
# For every template present in `split` the table records the cited rule,
# whether one of its keywords really occurs in the template, whether its label
# agrees with the answer, and what the keyword decision list (the first rule
# whose keyword occurs) would have decided. A faithful model cites that first
# matching rule. Citing a rule whose keywords are absent means the model
# applied the rule's sentence more loosely than the verified keywords allow.
# `rules` must be the ranked rules shown in the prompt.
def rule_faithfulness(
    decisions: pd.DataFrame, rules: list[Rule], split: str = "test"
) -> tuple[dict[str, int], pd.DataFrame]:
    keyword = KeywordClassifier(rules)
    rows = []
    for row in decisions[decisions[f"n_{split}"] > 0].itertuples(index=False):
        text = str(row.template)
        cited = _rule_number(row.rule, len(rules))
        rule = rules[cited - 1] if cited else None
        first = keyword.classify(int(row.template_id), text)
        rows.append(
            {
                "template_id": int(row.template_id),
                "template": text,
                "label": int(row.label),
                "cited_rule": cited,
                "cited_rule_occurs": bool(rule and rule.matches(text)),
                "cited_rule_label_agrees": bool(rule and rule.label == int(row.label)),
                "keyword_rule": first.rule,
                "keyword_label": first.label,
                "has_alert": bool(getattr(row, f"n_{split}_anomalous") > 0),
                "n_messages": int(getattr(row, f"n_{split}")),
                "reason": row.reason,
            }
        )
    table = pd.DataFrame(rows)
    if table.empty:
        return {}, table

    cites = table["cited_rule"].notna()
    abnormal = table["label"] == 1
    extra = abnormal & (table["keyword_label"] == 0)
    summary = {
        "templates": len(table),
        "answers_citing_a_rule": int(cites.sum()),
        "cited_rule_occurs_in_template": int((cites & table["cited_rule_occurs"]).sum()),
        "cited_rule_label_agrees": int((cites & table["cited_rule_label_agrees"]).sum()),
        "cited_rule_is_keyword_rule": int(
            (cites & (table["cited_rule"] == table["keyword_rule"])).sum()
        ),
        "abnormal_answers": int(abnormal.sum()),
        "abnormal_answers_backed_by_an_occurring_rule": int(
            (abnormal & table["cited_rule_occurs"] & table["cited_rule_label_agrees"]).sum()
        ),
        "abnormal_where_keywords_say_normal": int(extra.sum()),
        "abnormal_where_keywords_say_normal_with_alerts": int((extra & table["has_alert"]).sum()),
        "normal_where_keywords_say_abnormal": int(
            ((table["label"] == 0) & (table["keyword_label"] == 1)).sum()
        ),
    }
    # Rule numbers as nullable integers, so tables show "4" rather than "4.0".
    for column in ("cited_rule", "keyword_rule"):
        table[column] = table[column].astype("Int64")
    return summary, table


# Window-level scores of the keyword decision list when it uses only the k
# highest-ranked rules, for every k in `ks` and every split in `splits`.
# Shows how much the top-k cut-off (30 in LogRules) matters. Choose k on the
# validation columns; the test columns are for reporting only.
def keyword_rule_sensitivity(
    logs: ProcessedLogs, rules: list[Rule], ks: list[int], splits: list[str]
) -> pd.DataFrame:
    template_ids = templates_in_splits(logs, splits)
    text = logs.template_text
    rows = []
    for k in sorted({min(k, len(rules)) for k in ks if k > 0}):
        classifier = KeywordClassifier(rules[:k])
        labels = {t: classifier.classify(t, text[t]).label for t in template_ids}
        row: dict[str, Any] = {"k": k}
        for split in splits:
            window = evaluate_template_labels(logs, labels, split)["window"]
            for name in ("precision", "recall", "f1", "fp"):
                row[f"{split}_{name}"] = window[name]
        rows.append(row)
    return pd.DataFrame(rows)


# Approach B's template-level mistakes from a decisions table (written by
# scripts/run_rule_guided.py): false negatives are templates with test alerts
# labeled normal; false positives are templates without test alerts labeled
# abnormal. Sorted by the number of test messages affected.
def template_errors(decisions: pd.DataFrame) -> pd.DataFrame:
    in_test = decisions[decisions["n_test"] > 0].copy()
    has_alerts = in_test["n_test_anomalous"] > 0
    in_test["error"] = np.select(
        [has_alerts & (in_test["label"] == 0), ~has_alerts & (in_test["label"] == 1)],
        ["false negative", "false positive"],
        default="",
    )
    errors = in_test[in_test["error"] != ""]
    columns = ["error", "template_id", "template", "n_test", "n_test_anomalous", "rule", "reason"]
    return errors.sort_values("n_test", ascending=False)[columns].reset_index(drop=True)
