"""Decision engine module.

Responsibility: resolve visual retrieval candidates into a final decision
state using confidence thresholds, and signal when secondary plugin
evidence should be gathered. This module must NEVER load neural network
weights or directly call the Detector/Retriever/PluginManager modules
(see 02_MODULE_SPECIFICATION.md, Section 5).

Plugin trigger policy (three independent reasons, any one is sufficient):
    - "uncertain": Top-1 similarity falls within the configured uncertain
      band (same as v0.1.0 behavior).
    - "ambiguous": among the first `ambiguous_top_n` candidates, the
      similarity spread (best - worst) is <= `ambiguous_margin` — the
      candidates are too close to separate on similarity alone.
    - "force": ANY candidate within the Retriever's Top-K (not just the
      winner) has a product_id listed in `plugins.force_rules`; the
      union of those plugin names becomes `forced_plugins`.

`evaluate_thresholds` is exposed as a side-effect-free method (reads only
`self._config`, never touches AI models or other modules) so `Reranker`
can reuse the exact same accept/uncertain/reject formula after
incorporating plugin evidence, without duplicating threshold logic in two
places (see 03_DEVELOPMENT_RULES.md, Rule 11 - Module Autonomy: this is
still DecisionEngine's own public method, not a private helper reached
into from outside).
"""

from __future__ import annotations

from src.core.config import AppConfig
from src.core.logger import get_logger
from src.core.utils import clip_coordinate, timer
from src.models.models import DecisionResult, RetrievalResult

logger = get_logger(__name__)

STATUS_ACCEPTED = "accepted"
STATUS_UNCERTAIN = "uncertain"
STATUS_REJECTED = "rejected"

REASON_UNCERTAIN = "uncertain"
REASON_AMBIGUOUS = "ambiguous"
REASON_FORCE = "force"


class DecisionEngine:
    """Applies rule-based thresholds to resolve retrieval candidates."""

    def __init__(self, config: AppConfig) -> None:
        """Initializes the DecisionEngine with its threshold configuration.

        Args:
            config: Fully validated application configuration.
        """
        self._config = config.decision
        self._plugins_enabled = config.plugins.enabled
        self._force_rules: dict[str, frozenset[str]] = {
            product_id: frozenset(plugin_names)
            for product_id, plugin_names in config.plugins.force_rules.items()
        }
        logger.info(
            "DecisionEngine initialized with similarity_threshold=%.2f "
            "min_confidence_accept=%.2f uncertain_band=%.2f "
            "ambiguous_top_n=%d ambiguous_margin=%.2f force_rules=%d",
            self._config.similarity_threshold,
            self._config.min_confidence_accept,
            self._config.uncertain_band,
            self._config.ambiguous_top_n,
            self._config.ambiguous_margin,
            len(self._force_rules),
        )
        
        logger.info("DecisionEngine force_rules content: %s", dict(self._force_rules))

    def evaluate_thresholds(self, similarity: float, detection_confidence: float) -> tuple[str, float]:
        """Applies the accept/uncertain/reject threshold formula.

        Pure with respect to pipeline state: reads only the configured
        thresholds, performs no I/O, and calls no other module. Exposed
        so `Reranker` can re-evaluate a candidate's status after
        incorporating plugin evidence without duplicating this formula.

        Args:
            similarity: A candidate's visual similarity score, in [0, 1].
            detection_confidence: The crop's detection confidence, in [0, 1].

        Returns:
            Tuple of (status, final_confidence) where status is one of
            "accepted", "uncertain", or "rejected".
        """
        final_confidence = clip_coordinate(
            self._config.detection_weight * detection_confidence
            + self._config.similarity_weight * similarity,
            0.0,
            1.0,
        )

        lower_band = self._config.similarity_threshold - self._config.uncertain_band

        if similarity >= self._config.similarity_threshold and final_confidence >= self._config.min_confidence_accept:
            return STATUS_ACCEPTED, final_confidence
        if similarity >= lower_band:
            return STATUS_UNCERTAIN, final_confidence
        return STATUS_REJECTED, final_confidence

    def _detect_ambiguous(self, retrieval_result: RetrievalResult) -> bool:
        """Checks whether the top candidates are too close to separate.

        Args:
            retrieval_result: Ranked candidates from the Retriever.

        Returns:
            True when the similarity spread among the first
            `ambiguous_top_n` candidates is <= `ambiguous_margin`.
        """
        top_n = retrieval_result.candidates[: self._config.ambiguous_top_n]
        if len(top_n) < 2:
            return False
        spread = top_n[0].similarity_score - top_n[-1].similarity_score
        return spread <= self._config.ambiguous_margin

    def _collect_forced_plugins(self, retrieval_result: RetrievalResult) -> frozenset[str]:
        """Collects the union of plugins forced by any Top-K candidate.

        Args:
            retrieval_result: Ranked candidates from the Retriever.

        Returns:
            Union of plugin names from `plugins.force_rules` for every
            candidate product_id present anywhere in the Top-K.
        """
        forced: set[str] = set()
        for candidate in retrieval_result.candidates:
            forced.update(self._force_rules.get(candidate.product_id, frozenset()))
        return frozenset(forced)

    def decide(self, retrieval_result: RetrievalResult) -> DecisionResult:
        """Resolves a RetrievalResult into a preliminary DecisionResult.

        This decision reflects similarity/detection-confidence thresholds
        plus the plugin trigger policy only. It does NOT incorporate
        plugin evidence — `Reranker` (invoked by `InventoryPipeline` when
        `needs_plugin` is True) produces the final DecisionResult.

        Args:
            retrieval_result: Ranked candidates returned by the Retriever,
                carrying a passthrough `detection_confidence` field.

        Returns:
            A DecisionResult describing the resolved product (if any),
            its consolidated confidence score, and whether/why plugin
            evidence should be gathered.
        """
        detection_confidence = retrieval_result.detection_confidence

        with timer() as elapsed:
            top_candidate = retrieval_result.top_candidate

            if top_candidate is None:
                result = DecisionResult(
                    crop_id=retrieval_result.crop_id,
                    product_id=None,
                    product_name=None,
                    detection_confidence=detection_confidence,
                    similarity_score=0.0,
                    final_confidence=0.0,
                    status=STATUS_REJECTED,
                    needs_plugin=False,
                    reason="No retrieval candidates available.",
                )
            else:
                similarity = top_candidate.similarity_score
                status, final_confidence = self.evaluate_thresholds(similarity, detection_confidence)

                ambiguous = self._detect_ambiguous(retrieval_result)
                forced_plugins = self._collect_forced_plugins(retrieval_result)

                trigger_reasons: set[str] = set()
                if status == STATUS_UNCERTAIN:
                    trigger_reasons.add(REASON_UNCERTAIN)
                if ambiguous:
                    trigger_reasons.add(REASON_AMBIGUOUS)
                if forced_plugins:
                    trigger_reasons.add(REASON_FORCE)

                needs_plugin = (
                    self._plugins_enabled
                    and bool(trigger_reasons)
                    and status != STATUS_REJECTED
                )

                reason_parts = []
                if REASON_UNCERTAIN in trigger_reasons:
                    lower_band = self._config.similarity_threshold - self._config.uncertain_band
                    reason_parts.append(
                        f"similarity {similarity:.3f} in uncertain band "
                        f"[{lower_band:.3f}, {self._config.similarity_threshold:.3f})"
                    )
                if REASON_AMBIGUOUS in trigger_reasons:
                    reason_parts.append(
                        f"top-{self._config.ambiguous_top_n} spread <= {self._config.ambiguous_margin:.3f}"
                    )
                if REASON_FORCE in trigger_reasons:
                    reason_parts.append(f"forced plugins {sorted(forced_plugins)}")
                reason = "; ".join(reason_parts) if reason_parts else (
                    f"similarity {similarity:.3f} and final confidence {final_confidence:.3f} "
                    "meet acceptance thresholds."
                    if status == STATUS_ACCEPTED
                    else f"similarity {similarity:.3f} below acceptance thresholds."
                )

                result = DecisionResult(
                    crop_id=retrieval_result.crop_id,
                    product_id=top_candidate.product_id if status != STATUS_REJECTED else None,
                    product_name=top_candidate.product_name if status != STATUS_REJECTED else None,
                    detection_confidence=detection_confidence,
                    similarity_score=similarity,
                    final_confidence=final_confidence,
                    status=status,
                    needs_plugin=needs_plugin,
                    trigger_reasons=frozenset(trigger_reasons),
                    forced_plugins=forced_plugins,
                    reason=reason,
                )

        result.processing_time_ms = elapsed["elapsed_ms"]

        if result.needs_plugin:
            logger.info(
                "DecisionEngine requests plugin evidence for crop_id='%s': "
                "reasons=%s forced_plugins=%s",
                retrieval_result.crop_id,
                sorted(result.trigger_reasons),
                sorted(result.forced_plugins),
            )

        logger.info(
            "DecisionEngine resolved crop_id='%s' -> status='%s' final_confidence=%.3f (%.2f ms)",
            retrieval_result.crop_id,
            result.status,
            result.final_confidence,
            elapsed["elapsed_ms"],
        )

        return result
