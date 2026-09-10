"""Unit tests for src.validation.metrics (METRIC_REGISTRY formulas)."""

from __future__ import annotations

from src.validation.metrics import (
    METRIC_REGISTRY,
    compute_metrics,
    confusion_matrix,
    f1,
    mean_rank,
    mrr,
    precision,
    recall,
)


def test_precision_recall_f1() -> None:
    """Basic TP/FP/FN formula sanity check."""
    stats = {"tp": 8, "fp": 2, "fn": 2}
    assert precision(stats) == 0.8
    assert recall(stats) == 0.8
    assert f1(stats) == 0.8


def test_precision_zero_division_returns_zero() -> None:
    """No predictions at all must not raise, and returns 0.0."""
    assert precision({"tp": 0, "fp": 0}) == 0.0
    assert recall({"tp": 0, "fn": 0}) == 0.0


def test_mrr_miss_contributes_zero_not_penalty() -> None:
    """A miss (rank=None) must contribute exactly 0.0 to MRR, not 1/(K+1)."""
    stats = {"ranks": [1, None]}
    # (1/1 + 0) / 2 = 0.5
    assert mrr(stats) == 0.5


def test_mean_rank_miss_penalized_as_k_plus_1() -> None:
    """A miss must be penalized as rank = top_k + 1 for mean_rank."""
    stats = {"ranks": [1, None], "top_k": 5}
    # (1 + 6) / 2 = 3.5
    assert mean_rank(stats) == 3.5


def test_confusion_matrix_builds_nested_counts() -> None:
    """confusion_matrix must build a nested {actual: {predicted: count}} dict."""
    stats = {"pairs": [("1", "1"), ("1", "2"), ("2", "2"), ("2", "2")]}
    matrix = confusion_matrix(stats)
    assert matrix["1"]["1"] == 1
    assert matrix["1"]["2"] == 1
    assert matrix["2"]["2"] == 2


def test_compute_metrics_computes_only_requested_names() -> None:
    """compute_metrics must compute exactly the requested metric names."""
    stats = {"tp": 5, "fp": 1, "fn": 1}
    result = compute_metrics(stats, ["precision", "recall"])
    assert set(result.keys()) == {"precision", "recall"}


def test_compute_metrics_unknown_name_does_not_crash() -> None:
    """An unknown metric name in config must degrade gracefully, not raise."""
    result = compute_metrics({}, ["totally_made_up_metric"])
    assert "unknown_metric" in result["totally_made_up_metric"]


def test_registry_contains_expected_core_metrics() -> None:
    """Sanity check that the registry exposes the documented metric set."""
    for name in ("precision", "recall", "f1", "mrr", "mean_rank", "top1_accuracy", "confusion_matrix"):
        assert name in METRIC_REGISTRY
