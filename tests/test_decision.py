"""Unit tests for src.decision.decision.DecisionEngine."""

from __future__ import annotations

from src.decision.decision import (
    REASON_AMBIGUOUS,
    REASON_FORCE,
    REASON_UNCERTAIN,
    STATUS_ACCEPTED,
    STATUS_REJECTED,
    STATUS_UNCERTAIN,
    DecisionEngine,
)
from src.models.models import RetrievalCandidate, RetrievalResult


def _make_retrieval_result(
    similarities: list[float],
    product_ids: list[str] | None = None,
    detection_confidence: float = 0.8,
) -> RetrievalResult:
    """Builds a RetrievalResult with one candidate per similarity score.

    Args:
        similarities: Similarity scores, in descending rank order.
        product_ids: Optional matching product_ids (defaults to "prod_0", "prod_1", ...).
        detection_confidence: Passthrough detection confidence.

    Returns:
        A RetrievalResult with `len(similarities)` ranked candidates.
    """
    ids = product_ids or [f"prod_{i}" for i in range(len(similarities))]
    candidates = [
        RetrievalCandidate(product_id=ids[i], product_name=ids[i], similarity_score=sim, rank=i + 1)
        for i, sim in enumerate(similarities)
    ]
    return RetrievalResult(crop_id="crop_1", candidates=candidates, detection_confidence=detection_confidence)


def test_decision_accepts_high_similarity(test_config) -> None:
    """High similarity + high detection confidence should yield 'accepted'."""
    engine = DecisionEngine(test_config)
    result = engine.decide(_make_retrieval_result([0.95], detection_confidence=0.9))

    assert result.status == STATUS_ACCEPTED
    assert result.product_id == "prod_0"
    assert result.needs_plugin is False
    assert result.trigger_reasons == frozenset()


def test_decision_rejects_low_similarity(test_config) -> None:
    """Very low similarity should yield 'rejected' with no product resolved."""
    engine = DecisionEngine(test_config)
    result = engine.decide(_make_retrieval_result([0.1]))

    assert result.status == STATUS_REJECTED
    assert result.product_id is None
    assert result.needs_plugin is False


def test_decision_uncertain_band_requests_plugin(test_config) -> None:
    """Similarity within the uncertain band should request plugin evidence."""
    engine = DecisionEngine(test_config)
    # threshold=0.55, uncertain_band=0.15 -> uncertain range is [0.40, 0.55)
    result = engine.decide(_make_retrieval_result([0.45]))

    assert result.status == STATUS_UNCERTAIN
    assert result.needs_plugin is True
    assert REASON_UNCERTAIN in result.trigger_reasons


def test_decision_handles_no_candidates(test_config) -> None:
    """An empty RetrievalResult must resolve to 'rejected' without crashing."""
    engine = DecisionEngine(test_config)
    empty_result = RetrievalResult(crop_id="crop_x", candidates=[], detection_confidence=0.5)

    result = engine.decide(empty_result)

    assert result.status == STATUS_REJECTED
    assert result.product_id is None


def test_decision_ambiguous_top_n_triggers_plugin(test_config) -> None:
    """Top-3 candidates within ambiguous_margin of each other should trigger 'ambiguous'."""
    engine = DecisionEngine(test_config)
    # ambiguous_margin=0.05; all three well above accept threshold individually,
    # but too close together to separate confidently.
    result = engine.decide(_make_retrieval_result([0.95, 0.93, 0.92]))

    assert REASON_AMBIGUOUS in result.trigger_reasons
    assert result.needs_plugin is True


def test_decision_not_ambiguous_when_well_separated(test_config) -> None:
    """A clear winner (large spread) must not be flagged as ambiguous."""
    engine = DecisionEngine(test_config)
    result = engine.decide(_make_retrieval_result([0.95, 0.50, 0.10]))

    assert REASON_AMBIGUOUS not in result.trigger_reasons


def test_decision_force_rule_triggers_regardless_of_similarity(test_config) -> None:
    """A Top-K candidate matching force_rules must trigger 'force', even if accepted."""
    engine = DecisionEngine(test_config)
    # test_config.plugins.force_rules = {"2": ["barcode"]}; product "2" is rank 2
    # here, not the winner, but must still trigger force per Top-K-wide policy.
    result = engine.decide(_make_retrieval_result([0.95, 0.93], product_ids=["1", "2"]))

    assert REASON_FORCE in result.trigger_reasons
    assert result.forced_plugins == frozenset({"barcode"})
    assert result.needs_plugin is True


def test_decision_no_force_when_product_absent_from_topk(test_config) -> None:
    """force_rules must not trigger if the listed product_id is absent from Top-K."""
    engine = DecisionEngine(test_config)
    result = engine.decide(_make_retrieval_result([0.95], product_ids=["999"]))

    assert REASON_FORCE not in result.trigger_reasons
    assert result.forced_plugins == frozenset()


def test_evaluate_thresholds_is_pure_and_reusable(test_config) -> None:
    """evaluate_thresholds must be callable independently, e.g. by Reranker."""
    engine = DecisionEngine(test_config)
    status, confidence = engine.evaluate_thresholds(similarity=0.95, detection_confidence=0.9)

    assert status == STATUS_ACCEPTED
    assert 0.0 <= confidence <= 1.0
