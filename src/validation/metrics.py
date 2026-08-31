"""VAL metric formulas.

Every metric is a small, pure function operating on a plain "stats" dict
gathered by `evaluator.py` for one stage (counts, lists of IoU/rank/
confidence values, etc.) — never on pipeline DTOs directly, and never
performing I/O. This keeps `evaluator.py` responsible only for *gathering*
per-stage statistics from a `PipelineTrace`, while this module owns every
formula.

Adding a new metric later is exactly two steps:
    1. Write a function `def my_metric(stats: dict) -> Any: ...` here.
    2. Add it to `METRIC_REGISTRY` under a stable string key.

`evaluator.py` then only needs `configs/config.yaml -> validation.metrics.<stage>`
to include that key — no other file changes.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Callable


def _safe_divide(numerator: float, denominator: float) -> float:
    """Divides two numbers, returning 0.0 instead of raising on zero division."""
    if denominator == 0:
        return 0.0
    return numerator / denominator


# =============================================================================
# Confusion-count-based metrics (need "tp", "fp", "fn" in stats)
# =============================================================================


def precision(stats: dict) -> float:
    """Precision = TP / (TP + FP)."""
    return round(_safe_divide(stats.get("tp", 0), stats.get("tp", 0) + stats.get("fp", 0)), 4)


def recall(stats: dict) -> float:
    """Recall = TP / (TP + FN)."""
    return round(_safe_divide(stats.get("tp", 0), stats.get("tp", 0) + stats.get("fn", 0)), 4)


def f1(stats: dict) -> float:
    """F1 = harmonic mean of precision and recall."""
    p, r = precision(stats), recall(stats)
    return round(_safe_divide(2 * p * r, p + r), 4)


def accuracy(stats: dict) -> float:
    """Accuracy = correct / total (needs "correct" and "total" in stats)."""
    return round(_safe_divide(stats.get("correct", 0), stats.get("total", 0)), 4)


def confusion_matrix(stats: dict) -> dict[str, dict[str, int]]:
    """Builds a nested {actual: {predicted: count}} confusion matrix.

    Needs `stats["pairs"]`: a list of (actual_label, predicted_label) tuples.
    """
    matrix: dict[str, dict[str, int]] = {}
    for actual, predicted in stats.get("pairs", []):
        matrix.setdefault(str(actual), Counter())[str(predicted)] += 1
    return {actual: dict(counts) for actual, counts in matrix.items()}


# =============================================================================
# Distribution-based metrics (need a list of raw values in stats)
# =============================================================================


def mean_iou(stats: dict) -> float:
    """Mean IoU over `stats["iou_values"]`."""
    values = stats.get("iou_values", [])
    return round(_safe_divide(sum(values), len(values)), 4)


def mean_value(stats: dict, key: str = "values") -> float:
    """Generic mean over `stats[key]`."""
    values = stats.get(key, [])
    return round(_safe_divide(sum(values), len(values)), 4)


def mean_latency_ms(stats: dict) -> float:
    """Mean latency over `stats["latencies"]`, in milliseconds."""
    values = stats.get("latencies", [])
    return round(_safe_divide(sum(values), len(values)), 3)


# =============================================================================
# Retrieval-specific metrics
# =============================================================================


def top1_accuracy(stats: dict) -> float:
    """Fraction of crops whose Top-1 candidate matches the GT product_id.

    Needs `stats["ranks"]`: list of 1-indexed GT rank per crop, or None
    when the GT product was not found in the returned Top-K.
    """
    ranks = stats.get("ranks", [])
    hits = sum(1 for rank in ranks if rank == 1)
    return round(_safe_divide(hits, len(ranks)), 4)


def topk_accuracy(stats: dict) -> float:
    """Fraction of crops where the GT product appears anywhere in the Top-K."""
    ranks = stats.get("ranks", [])
    hits = sum(1 for rank in ranks if rank is not None)
    return round(_safe_divide(hits, len(ranks)), 4)


def recall_at_k(stats: dict) -> float:
    """Alias of topk_accuracy — Recall@K over `stats["ranks"]`."""
    return topk_accuracy(stats)


def mrr(stats: dict) -> float:
    """Mean Reciprocal Rank. Contributes 0.0 (not 1/(K+1)) for a miss."""
    ranks = stats.get("ranks", [])
    reciprocal_sum = sum((1.0 / rank) if rank is not None else 0.0 for rank in ranks)
    return round(_safe_divide(reciprocal_sum, len(ranks)), 4)


def mean_rank(stats: dict) -> float:
    """Mean GT rank. A miss is penalized as rank = K + 1 (per project decision)."""
    ranks = stats.get("ranks", [])
    top_k = stats.get("top_k", 5)
    effective = [rank if rank is not None else top_k + 1 for rank in ranks]
    return round(_safe_divide(sum(effective), len(effective)), 4)


# =============================================================================
# Trigger / execution-rate metrics (Plugins, Fusion)
# =============================================================================


def trigger_rate(stats: dict) -> float:
    """Fraction of eligible crops for which this plugin was triggered."""
    return round(_safe_divide(stats.get("triggered", 0), stats.get("eligible", 0)), 4)


def success_rate(stats: dict) -> float:
    """Fraction of executed plugin runs that produced usable evidence."""
    return round(_safe_divide(stats.get("success", 0), stats.get("executed", 0)), 4)


def correction_rate(stats: dict) -> float:
    """Fraction of executed cases where the plugin/fusion changed a wrong
    decision into a correct one (needs "corrected" and "executed")."""
    return round(_safe_divide(stats.get("corrected", 0), stats.get("executed", 0)), 4)


def degradation_rate(stats: dict) -> float:
    """Fraction of executed cases where the plugin/fusion changed a correct
    decision into a wrong one (needs "degraded" and "executed")."""
    return round(_safe_divide(stats.get("degraded", 0), stats.get("executed", 0)), 4)


def valid_rate(stats: dict) -> float:
    """Fraction of crops considered valid (needs "valid" and "total")."""
    return round(_safe_divide(stats.get("valid", 0), stats.get("total", 0)), 4)


def improved_rate(stats: dict) -> float:
    """Fraction of refined boxes whose IoU-to-GT improved (needs "improved"/"total")."""
    return round(_safe_divide(stats.get("improved", 0), stats.get("total", 0)), 4)


def iou_improvement(stats: dict) -> float:
    """Mean (iou_after - iou_before) over refined detections.

    Needs `stats["iou_before"]` and `stats["iou_after"]`, parallel lists.
    """
    before = stats.get("iou_before", [])
    after = stats.get("iou_after", [])
    if not before or len(before) != len(after):
        return 0.0
    deltas = [a - b for a, b in zip(after, before)]
    return round(_safe_divide(sum(deltas), len(deltas)), 4)


def accuracy_before(stats: dict) -> float:
    """Accuracy using `stats["correct_before"]` / `stats["total"]`."""
    return round(_safe_divide(stats.get("correct_before", 0), stats.get("total", 0)), 4)


def accuracy_after(stats: dict) -> float:
    """Accuracy using `stats["correct_after"]` / `stats["total"]`."""
    return round(_safe_divide(stats.get("correct_after", 0), stats.get("total", 0)), 4)


def improvement(stats: dict) -> float:
    """accuracy_after - accuracy_before."""
    return round(accuracy_after(stats) - accuracy_before(stats), 4)


# =============================================================================
# Registry
# =============================================================================

METRIC_REGISTRY: dict[str, Callable[[dict], Any]] = {
    "precision": precision,
    "recall": recall,
    "f1": f1,
    "accuracy": accuracy,
    "confusion_matrix": confusion_matrix,
    "mean_iou": mean_iou,
    "mean_latency_ms": mean_latency_ms,
    "top1_accuracy": top1_accuracy,
    "topk_accuracy": topk_accuracy,
    "recall_at_k": recall_at_k,
    "mrr": mrr,
    "mean_rank": mean_rank,
    "trigger_rate": trigger_rate,
    "success_rate": success_rate,
    "correction_rate": correction_rate,
    "degradation_rate": degradation_rate,
    "valid_rate": valid_rate,
    "improved_rate": improved_rate,
    "iou_improvement": iou_improvement,
    "accuracy_before": accuracy_before,
    "accuracy_after": accuracy_after,
    "improvement": improvement,
}


def compute_metrics(stats: dict, metric_names: list[str]) -> dict[str, Any]:
    """Computes every requested metric for one stage's gathered stats.

    Args:
        stats: Raw per-stage statistics gathered by the Evaluator.
        metric_names: Metric keys to compute, from `configs/config.yaml
            -> validation.metrics.<stage>`. Unknown keys are skipped with
            an explanatory value rather than raising, so a config typo
            never crashes a full validation run.

    Returns:
        Mapping of metric name to its computed value.
    """
    results: dict[str, Any] = {}
    for name in metric_names:
        formula = METRIC_REGISTRY.get(name)
        if formula is None:
            results[name] = f"unknown_metric: '{name}'"
            continue
        results[name] = formula(stats)
    return results
