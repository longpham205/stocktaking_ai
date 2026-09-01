"""Reranking / evidence fusion module.

Responsibility:
    Fuse secondary evidence from plugins (OCR / color / barcode) across
    the full Retriever Top-K and produce the final DecisionResult.

OCR orientation handling:
    - OCR may provide Top-1 or Top-2 orientation candidates.
    - Each orientation is matched independently against every retrieval candidate.
    - The strongest OCR evidence for each product is selected.
    - Effective OCR evidence follows:

        effective evidence = match strength × plugin confidence

      For ambiguous OCR orientations, the orientation-specific OCR confidence
      is preferred over the global OCR confidence.

Barcode matching:
    - Decoded values and catalog values are both normalized before
      comparison (digits only, UPC-A aligned to EAN-13 by prepending a
      leading zero) since the two encode the same identifier and
      catalog/decoder data commonly mixes the two forms.
"""

from __future__ import annotations

import difflib
import json
import re
from pathlib import Path
from typing import Any, Callable

import cv2
import numpy as np

from src.core.config import AppConfig
from src.core.logger import get_logger
from src.core.utils import clip_coordinate, timer
from src.decision.decision import STATUS_ACCEPTED, STATUS_UNCERTAIN, DecisionEngine
from src.models.models import (
    DecisionResult,
    PluginResult,
    RetrievalCandidate,
    RetrievalResult,
)

logger = get_logger(__name__)


class Reranker:
    """Fuse plugin evidence across the full Retriever Top-K."""

    def __init__(
        self,
        config: AppConfig,
        decision_engine: DecisionEngine,
        product_lookup: Callable[[str], dict | None],
        color_reference_lookup: Callable[[str], list[float] | None] | None = None,
    ) -> None:
        self._decision_engine = decision_engine
        self._product_lookup = product_lookup
        self._color_reference_lookup = color_reference_lookup
        self._min_confidence_accept = config.decision.min_confidence_accept

        # Retrieval Protection
        rp = config.rerank.retrieval_protection
        self._retrieval_protection_enabled = rp.enabled
        self._consensus_mode = rp.consensus_mode
        self._retrieval_hybrid_count_weight = float(rp.hybrid_weights.count)
        self._retrieval_hybrid_weighted_weight = float(rp.hybrid_weights.weighted)
        self._retrieval_hybrid_margin_weight = float(rp.hybrid_weights.margin)
        self._retrieval_consensus_start = rp.consensus_start
        self._retrieval_consensus_strong = rp.consensus_strong
        self._margin_consensus_start = rp.margin_consensus_start
        self._margin_consensus_strong = rp.margin_consensus_strong
        self._retrieval_boost_ratio_min = rp.boost_ratio_min
        self._retrieval_boost_ratio_max = rp.boost_ratio_max
        self._retrieval_color_strong_ratio = rp.color_strong_ratio
        self._retrieval_ocr_strong_ratio = rp.ocr_strong_ratio
        self._retrieval_barcode_strong_ratio = rp.barcode_strong_ratio
        self._min_switch_margin = rp.min_switch_margin

        # Plugin Weights & Parameters
        self._barcode_weight = float(config.rerank.barcode.weight)

        ocr = config.rerank.ocr
        self._ocr_weight = float(ocr.weight)
        self._ocr_min_text_length = int(ocr.min_text_length)
        self._ocr_fuzzy_threshold = float(ocr.fuzzy_threshold)

        color = config.rerank.color
        self._color_enabled = color.enabled
        self._color_weight = float(color.weight)
        self._color_delta_e_strong = color.delta_e_strong
        self._color_delta_e_weak = color.delta_e_weak
        self._color_min_margin = color.min_margin
        self._color_margin_scale = color.margin_scale
        self._color_l_weight = color.l_weight
        self._color_reference_path = Path(color.references_path)
        self._color_reference = self._load_color_reference(self._color_reference_path)

        # Confusable Pairs
        self._confusable_pairs = [
            frozenset(map(str, pair)) for pair in config.rerank.confusable_pairs
        ]
        self._confusable_min_agree = config.rerank.confusable_min_agreeing_plugins

        # Caches
        self._product_tokens_cache: dict[str, list[str]] = {}
        self._product_color_code_cache: dict[str, str | None] = {}

    # ----------------------------------------------------------------------
    # PUBLIC API
    # ----------------------------------------------------------------------

    def rerank(
        self,
        retrieval_result: RetrievalResult,
        plugin_result: PluginResult,
    ) -> DecisionResult:
        """Rerank Retriever Top-K candidates using plugin evidence."""
        with timer() as elapsed:
            if not retrieval_result.candidates:
                result = DecisionResult(
                    crop_id=retrieval_result.crop_id,
                    product_id=None,
                    product_name=None,
                    detection_confidence=retrieval_result.detection_confidence,
                    similarity_score=0.0,
                    final_confidence=0.0,
                    status="rejected",
                    needs_plugin=False,
                    reason="No retrieval candidates available.",
                )
                result.rerank_debug = None
            else:
                result = self._rerank_with_candidates(retrieval_result, plugin_result)

        result.rerank_latency_ms = elapsed["elapsed_ms"]
        guard = (getattr(result, "rerank_debug", None) or {}).get("retrieval_guard", {})

        logger.info(
            "Reranker finalized crop_id='%s' -> product_id=%s status='%s' "
            "final_confidence=%.3f retrieval_consensus=%s consensus_winner=%s (%.2f ms)",
            retrieval_result.crop_id,
            result.product_id,
            result.status,
            result.final_confidence,
            guard.get("consensus"),
            guard.get("consensus_winner"),
            elapsed["elapsed_ms"],
        )
        return result

    # ----------------------------------------------------------------------
    # MAIN RERANKING LOGIC
    # ----------------------------------------------------------------------

    def _rerank_with_candidates(
        self,
        retrieval_result: RetrievalResult,
        plugin_result: PluginResult,
    ) -> DecisionResult:
        """Score every Retriever Top-K candidate using plugin evidence."""
        barcode_matches = self._extract_barcode_matches(plugin_result)
        ocr_candidates = self._extract_ocr_candidates(plugin_result)
        query_lab = self._extract_color_lab(plugin_result)
        plugin_confidence = self._extract_plugin_confidences(plugin_result)

        retrieval_guard = self._build_retrieval_guard(retrieval_result.candidates)
        color_distances = self._calculate_color_distances(retrieval_result.candidates, query_lab)
        color_context = self._build_color_context(color_distances)

        scored: list[tuple[float, RetrievalCandidate, dict[str, Any]]] = []

        for candidate in retrieval_result.candidates:
            product_id = str(candidate.product_id)
            base_score = float(candidate.similarity_score)

            # Barcode
            barcode_match_strength = self._barcode_match_strength(product_id, barcode_matches)

            # OCR: Each orientation evaluated independently. Strongest evidence wins.
            ocr_evidence = self._best_ocr_evidence(
                candidate, ocr_candidates, global_plugin_confidence=plugin_confidence["ocr"]
            )
            ocr_match_strength = float(ocr_evidence["match_strength"])
            ocr_confidence = float(ocr_evidence["plugin_confidence"])

            # Color
            color_match_strength = self._color_match_strength(product_id, color_distances, color_context)

            # Retrieval Protection
            barcode_ratio = self._plugin_boost_ratio(product_id, retrieval_guard, "barcode")
            ocr_ratio = self._plugin_boost_ratio(product_id, retrieval_guard, "ocr")
            color_ratio = self._plugin_boost_ratio(product_id, retrieval_guard, "color")

            # Effective Evidence Boosts
            barcode_boost = self._effective_boost(
                barcode_match_strength, plugin_confidence["barcode"], self._barcode_weight, barcode_ratio
            )
            ocr_boost = self._effective_boost(
                ocr_match_strength, ocr_confidence, self._ocr_weight, ocr_ratio
            )
            color_boost = self._effective_boost(
                color_match_strength, plugin_confidence["color"], self._color_weight, color_ratio
            )

            final_score = base_score + barcode_boost + ocr_boost + color_boost

            evidence: dict[str, Any] = {
                "base": base_score,
                "barcode": barcode_boost,
                "ocr": ocr_boost,
                "color": color_boost,
                "barcode_match_strength": barcode_match_strength,
                "ocr_match_strength": ocr_match_strength,
                "color_match_strength": color_match_strength,
                "barcode_plugin_confidence": plugin_confidence["barcode"],
                "ocr_plugin_confidence": plugin_confidence["ocr"],
                "color_plugin_confidence": plugin_confidence["color"],
                "ocr_effective_plugin_confidence": ocr_confidence,
                "ocr_selected_rotation": ocr_evidence["rotation"],
                "ocr_selected_text": ocr_evidence["text"],
                "ocr_selected_score": ocr_evidence["orientation_score"],
                "ocr_selected_information_score": ocr_evidence["information_score"],
                "ocr_orientation_match_candidates": ocr_evidence["orientation_match_candidates"],
                "barcode_plugin_weight": self._barcode_weight,
                "ocr_plugin_weight": self._ocr_weight,
                "color_plugin_weight": self._color_weight,
                "barcode_retrieval_ratio": barcode_ratio,
                "ocr_retrieval_ratio": ocr_ratio,
                "color_retrieval_ratio": color_ratio,
                "final": final_score,
            }
            scored.append((final_score, candidate, evidence))

        scored.sort(key=lambda item: item[0], reverse=True)
        winner_score, winner, winner_evidence = scored[0]

        # Switch Protection
        original_top1_id = str(retrieval_result.top_candidate.product_id)
        switch_reverted = False

        if str(winner.product_id) != original_top1_id:
            original_entry = next(
                (e for e in scored if str(e[1].product_id) == original_top1_id), None
            )
            if original_entry is not None and (winner_score - original_entry[0]) < self._min_switch_margin:
                winner_score, winner, winner_evidence = original_entry
                switch_reverted = True

        # Decision Thresholds
        status, final_confidence = self._decision_engine.evaluate_thresholds(
            winner.similarity_score, retrieval_result.detection_confidence
        )
        evidence_boost = max(0.0, winner_score - winner.similarity_score)
        final_confidence = clip_coordinate(final_confidence + evidence_boost, 0.0, 1.0)

        # Decision Reason
        reason = f"Reranked using plugin evidence plugins={plugin_result.executed_plugins}."
        if winner_evidence["barcode"] > 0.0:
            reason += f" Exact barcode match (effective_boost={winner_evidence['barcode']:.4f})."

        if winner_evidence["ocr"] > 0.0:
            selected_text = str(winner_evidence.get("ocr_selected_text", ""))
            selected_rotation = int(winner_evidence.get("ocr_selected_rotation", 0))
            reason += (
                f" OCR matched catalog identifier (effective_boost={winner_evidence['ocr']:.4f}, "
                f"text='{selected_text}', rotation={selected_rotation})."
            )

        if winner_evidence["color"] > 0.0:
            color_code = self._extract_product_color_code(winner.product_id)
            if color_code:
                reason += f" Color matched catalog code {color_code} (effective_boost={winner_evidence['color']:.4f})."
            else:
                reason += f" Color evidence matched candidate (effective_boost={winner_evidence['color']:.4f})."

        if color_context.get("available", False):
            reason += f" Color ΔE={float(color_context['best_delta_e']):.2f}."

        if switch_reverted:
            reason += " Plugin evidence for a competing candidate was below min_switch_margin; kept original Top-1."

        if status == STATUS_UNCERTAIN and final_confidence >= self._min_confidence_accept:
            status = STATUS_ACCEPTED
            reason += " Upgraded to accepted after evidence fusion."

        # --------------------------------------------------------------
        # Confusable-Pair Guard (FIXED)
        #
        # Old behavior: downgraded to `uncertain` whenever fewer than
        # `confusable_min_agreeing_plugins` plugins showed positive
        # match_strength for the WINNER — even when that was simply
        # because the only eligible plugin (e.g. OCR) produced no
        # evidence for either side that crop. Silence was being treated
        # as disagreement.
        #
        # New behavior: only downgrade when the OTHER member of the
        # confusable pair actually shows up among this crop's retrieval
        # candidates AND has strictly stronger plugin evidence than the
        # winner on at least one plugin. Absence of evidence on both
        # sides is no longer punished.
        # --------------------------------------------------------------
        conflict_detected = False
        opponent_evidence: dict[str, Any] | None = None
        confusable_pair = self._confusable_pair_for(winner.product_id)

        if confusable_pair is not None and status == STATUS_ACCEPTED:
            conflict_detected, opponent_evidence = self._confusable_pair_conflict(
                confusable_pair, winner, winner_evidence, scored
            )

            if conflict_detected:
                status = STATUS_UNCERTAIN
                other_id = next(iter(confusable_pair - {str(winner.product_id)}), None)
                reason += (
                    f" Downgraded to uncertain: competing candidate {other_id} in confusable "
                    f"pair {sorted(confusable_pair)} has stronger plugin evidence "
                    f"(winner_ocr={winner_evidence['ocr_match_strength']:.2f}, "
                    f"opponent_ocr={(opponent_evidence or {}).get('ocr_match_strength', 0.0):.2f})."
                )

        # Final Result & Debug Construction
        result = DecisionResult(
            crop_id=retrieval_result.crop_id,
            product_id=winner.product_id if status != "rejected" else None,
            product_name=winner.product_name if status != "rejected" else None,
            detection_confidence=retrieval_result.detection_confidence,
            similarity_score=winner.similarity_score,
            final_confidence=final_confidence,
            status=status,
            needs_plugin=False,
            reason=reason,
        )

        result.rerank_debug = {
            "retrieval_guard": retrieval_guard,
            "barcode_preprocessing_stage": plugin_result.evidence.get("barcode", {}).get(
                "preprocessing_stage"
            ),
            "switch_reverted": switch_reverted,
            "plugin_confidence": plugin_confidence,
            "ocr_orientation_candidates": ocr_candidates,
            "confusable_pair": sorted(confusable_pair) if confusable_pair else None,
            "confusable_conflict_detected": conflict_detected,
            "confusable_opponent_evidence": opponent_evidence,
            "candidates": [
                {
                    "product_id": c.product_id,
                    "base_similarity": ev["base"],
                    "barcode_boost": ev["barcode"],
                    "ocr_boost": ev["ocr"],
                    "color_boost": ev["color"],
                    "barcode_match_strength": ev["barcode_match_strength"],
                    "ocr_match_strength": ev["ocr_match_strength"],
                    "color_match_strength": ev["color_match_strength"],
                    "barcode_plugin_confidence": ev["barcode_plugin_confidence"],
                    "ocr_plugin_confidence": ev["ocr_plugin_confidence"],
                    "ocr_effective_plugin_confidence": ev["ocr_effective_plugin_confidence"],
                    "color_plugin_confidence": ev["color_plugin_confidence"],
                    "ocr_selected_rotation": ev["ocr_selected_rotation"],
                    "ocr_selected_text": ev["ocr_selected_text"],
                    "ocr_selected_score": ev["ocr_selected_score"],
                    "ocr_selected_information_score": ev["ocr_selected_information_score"],
                    "ocr_orientation_match_candidates": ev["ocr_orientation_match_candidates"],
                    "barcode_plugin_weight": ev["barcode_plugin_weight"],
                    "ocr_plugin_weight": ev["ocr_plugin_weight"],
                    "color_plugin_weight": ev["color_plugin_weight"],
                    "barcode_retrieval_ratio": ev["barcode_retrieval_ratio"],
                    "ocr_retrieval_ratio": ev["ocr_retrieval_ratio"],
                    "color_retrieval_ratio": ev["color_retrieval_ratio"],
                    "adjusted_score": ev["final"],
                    "color_code": self._extract_product_color_code(c.product_id),
                    "delta_e": color_distances.get(str(c.product_id)),
                }
                for _, c, ev in scored
            ],
            "rank_before": [c.product_id for c in retrieval_result.candidates],
            "rank_after": [c.product_id for _, c, _ in scored],
        }

        return result

    # ----------------------------------------------------------------------
    # EFFECTIVE EVIDENCE HELPERS
    # ----------------------------------------------------------------------

    @staticmethod
    def _effective_boost(
        match_strength: float,
        plugin_confidence: float,
        plugin_weight: float,
        retrieval_ratio: float,
    ) -> float:
        """Calculate effective plugin evidence boost."""
        effective_evidence = max(match_strength, 0.0) * np.clip(plugin_confidence, 0.0, 1.0)
        return float(
            effective_evidence * max(plugin_weight, 0.0) * np.clip(retrieval_ratio, 0.0, 1.0)
        )

    @staticmethod
    def _extract_plugin_confidences(plugin_result: PluginResult) -> dict[str, float]:
        """Extract runtime confidence supplied by PluginManager."""
        result = {"ocr": 0.0, "color": 0.0, "barcode": 0.0}

        for plugin_name in result:
            evidence = plugin_result.evidence.get(plugin_name)
            if not isinstance(evidence, dict):
                continue

            confidence = evidence.get("confidence")
            if confidence is None:
                confidence = evidence.get("plugin_confidence")

            if confidence is not None:
                try:
                    val = float(confidence)
                    if np.isfinite(val):
                        result[plugin_name] = float(np.clip(val, 0.0, 1.0))
                except (TypeError, ValueError):
                    pass

        top_level = getattr(plugin_result, "plugin_confidence", None)
        if isinstance(top_level, dict):
            for plugin_name in result:
                if result[plugin_name] > 0.0:
                    continue
                try:
                    val = float(top_level.get(plugin_name))
                    if np.isfinite(val):
                        result[plugin_name] = float(np.clip(val, 0.0, 1.0))
                except (TypeError, ValueError):
                    pass

        logger.debug(
            "Plugin confidences extracted: OCR=%.3f Color=%.3f Barcode=%.3f",
            result["ocr"],
            result["color"],
            result["barcode"],
        )
        return result

    # ----------------------------------------------------------------------
    # OCR EXTRACTION
    # ----------------------------------------------------------------------

    @classmethod
    def _parse_ocr_dict(cls, data: dict[str, Any], text_fallback: str = "") -> dict[str, Any]:
        """Helper to safely parse individual OCR dict structures."""
        text = str(data.get("text", text_fallback)).strip()
        return {
            "rotation": cls._safe_int(data.get("rotation", 0), 0),
            "score": cls._safe_float(data.get("score", 0.0), 0.0),
            "confidence": cls._safe_float(data.get("confidence", 0.0), 0.0),
            "max_fragment_confidence": cls._safe_float(data.get("max_fragment_confidence", 0.0), 0.0),
            "mean_fragment_confidence": cls._safe_float(data.get("mean_fragment_confidence", 0.0), 0.0),
            "information_score": cls._safe_float(data.get("information_score", 0.0), 0.0),
            "text": text,
            "text_length": cls._safe_int(data.get("text_length", len(text)), len(text)),
            "useful_text_length": cls._safe_int(data.get("useful_text_length", 0), 0),
            "useful_fragment_count": cls._safe_int(data.get("useful_fragment_count", 0), 0),
            "fragments": data.get("fragments", []),
        }

    @classmethod
    def _extract_ocr_candidates(cls, plugin_result: PluginResult) -> list[dict[str, Any]]:
        """Extract OCR orientation candidates with fallback."""
        ocr_evidence = plugin_result.evidence.get("ocr")
        if not isinstance(ocr_evidence, dict):
            return []

        raw_candidates = ocr_evidence.get("orientation_candidates")
        if isinstance(raw_candidates, list):
            candidates = [
                cls._parse_ocr_dict(c)
                for c in raw_candidates
                if isinstance(c, dict) and str(c.get("text", "")).strip()
            ]
            if candidates:
                return candidates

        # Backward-compatible single OCR result fallback
        single_text = str(ocr_evidence.get("text", "")).strip()
        if single_text:
            return [cls._parse_ocr_dict(ocr_evidence, text_fallback=single_text)]

        return []

    def _best_ocr_evidence(
        self,
        candidate: RetrievalCandidate,
        ocr_candidates: list[dict[str, Any]],
        global_plugin_confidence: float,
    ) -> dict[str, Any]:
        """Select strongest OCR evidence for one retrieval candidate."""
        empty = {
            "match_strength": 0.0,
            "plugin_confidence": 0.0,
            "effective_evidence": 0.0,
            "rotation": 0,
            "text": "",
            "orientation_score": 0.0,
            "information_score": 0.0,
            "orientation_match_candidates": [],
        }

        if not ocr_candidates:
            return empty

        orientation_matches: list[dict[str, Any]] = []

        for index, orientation in enumerate(ocr_candidates):
            text = str(orientation.get("text", "")).strip()
            if not text:
                continue

            match_strength = self._ocr_text_match_strength(candidate, text)
            orientation_confidence = self._resolve_ocr_orientation_confidence(
                orientation, global_plugin_confidence
            )
            effective_evidence = match_strength * orientation_confidence

            orientation_matches.append({
                "index": index,
                "rotation": self._safe_int(orientation.get("rotation", 0), 0),
                "text": text,
                "match_strength": float(match_strength),
                "plugin_confidence": float(orientation_confidence),
                "effective_evidence": float(effective_evidence),
                "orientation_score": self._safe_float(orientation.get("score", 0.0), 0.0),
                "information_score": self._safe_float(orientation.get("information_score", 0.0), 0.0),
            })

        if not orientation_matches:
            return empty

        selected = max(
            orientation_matches,
            key=lambda item: (
                item["effective_evidence"],
                item["match_strength"],
                item["information_score"],
                item["orientation_score"],
            ),
        )

        logger.debug(
            "OCR candidate=%s selected orientation=%d text='%s' match=%.4f confidence=%.4f "
            "effective=%.4f information=%.4f score=%.4f",
            candidate.product_id,
            selected["rotation"],
            selected["text"],
            selected["match_strength"],
            selected["plugin_confidence"],
            selected["effective_evidence"],
            selected["information_score"],
            selected["orientation_score"],
        )

        return {
            "match_strength": float(selected["match_strength"]),
            "plugin_confidence": float(selected["plugin_confidence"]),
            "effective_evidence": float(selected["effective_evidence"]),
            "rotation": int(selected["rotation"]),
            "text": selected["text"],
            "orientation_score": float(selected["orientation_score"]),
            "information_score": float(selected["information_score"]),
            "orientation_match_candidates": orientation_matches,
        }

    @staticmethod
    def _resolve_ocr_orientation_confidence(
        orientation: dict[str, Any],
        global_plugin_confidence: float,
    ) -> float:
        """Resolve confidence for one OCR orientation."""
        confidence = orientation.get("confidence")
        try:
            val = float(confidence)
            if np.isfinite(val):
                return float(np.clip(val, 0.0, 1.0))
        except (TypeError, ValueError):
            pass

        return float(np.clip(global_plugin_confidence, 0.0, 1.0))

    def _ocr_text_match_strength(
        self,
        candidate: RetrievalCandidate,
        ocr_text: str,
    ) -> float:
        """Calculate OCR text-to-catalog match strength."""
        if not ocr_text:
            return 0.0

        normalized_ocr = self._normalize_text(ocr_text)
        if len(normalized_ocr) < self._ocr_min_text_length:
            return 0.0

        product = self._product_lookup(candidate.product_id)
        reference_name = (
            str(product.get("product_name", "")) if product else candidate.product_name
        )
        normalized_name = self._normalize_text(reference_name)

        if not normalized_name:
            return 0.0

        catalog_tokens = self._extract_catalog_tokens(reference_name)
        if catalog_tokens:
            token_strength = self._token_match_strength(normalized_ocr, catalog_tokens)
            if token_strength > 0.0:
                return token_strength

        if normalized_ocr in normalized_name:
            coverage = min(len(normalized_ocr) / max(len(normalized_name), 1), 1.0)
            return float(np.clip(coverage, 0.0, 1.0))

        ratio = difflib.SequenceMatcher(None, normalized_ocr, normalized_name).ratio()
        if ratio < self._ocr_fuzzy_threshold:
            return 0.0

        return float(np.clip(ratio, 0.0, 1.0))

    def _ocr_match_strength(
        self,
        candidate: RetrievalCandidate,
        ocr_text: str,
    ) -> float:
        """Backward-compatible OCR match-strength helper."""
        return self._ocr_text_match_strength(candidate, ocr_text)

    def _extract_catalog_tokens(self, product_name: str) -> list[str]:
        """Extract discriminative alphanumeric catalog tokens."""
        cached = self._product_tokens_cache.get(product_name)
        if cached is not None:
            return cached

        tokens: list[str] = []
        for token in re.findall(r"[A-Za-z0-9]+", product_name):
            token = token.upper().strip()
            if token and len(token) >= self._ocr_min_text_length:
                tokens.append(token)

        tokens = list(dict.fromkeys(tokens))
        self._product_tokens_cache[product_name] = tokens
        return tokens

    def _token_match_strength(self, ocr_text: str, catalog_tokens: list[str]) -> float:
        """Return OCR catalog-token match strength in [0, 1]."""
        if not catalog_tokens:
            return 0.0

        for token in catalog_tokens:
            if ocr_text == token or token in ocr_text:
                return 1.0

        for token in catalog_tokens:
            if ocr_text in token:
                coverage = len(ocr_text) / max(len(token), 1)
                if coverage >= 0.5:
                    return float(np.clip(coverage, 0.0, 1.0))

        best_ratio = max(
            (difflib.SequenceMatcher(None, ocr_text, token).ratio() for token in catalog_tokens),
            default=0.0,
        )

        if best_ratio >= self._ocr_fuzzy_threshold:
            return float(np.clip(best_ratio, 0.0, 1.0))

        return 0.0

    # ----------------------------------------------------------------------
    # RETRIEVAL PROTECTION
    # ----------------------------------------------------------------------

    @staticmethod
    def _scale(value: float, lo: float, hi: float) -> float:
        """Normalize value to [0, 1]."""
        return float(np.clip((value - lo) / max(hi - lo, 1e-6), 0.0, 1.0))

    def _build_retrieval_guard(
        self,
        candidates: list[RetrievalCandidate],
    ) -> dict[str, float | bool | str | None]:
        """Build Retriever consensus and protection guard signals."""
        if not candidates:
            return {
                "enabled": self._retrieval_protection_enabled,
                "available": False,
                "consensus_winner": None,
                "consensus": 0.0,
                "identity_count_consensus": 0.0,
                "identity_weighted_consensus": 0.0,
                "margin_consensus": 0.0,
                "count_signal": 0.0,
                "weighted_signal": 0.0,
                "margin_signal": 0.0,
                "consensus_agreement": 0.0,
                "protection_strength": 0.0,
                "candidate_count": 0.0,
            }

        counts: dict[str, int] = {}
        sims: dict[str, float] = {}
        first_index: dict[str, int] = {}
        total_sim = 0.0

        for index, candidate in enumerate(candidates):
            pid = str(candidate.product_id)
            sim = max(float(candidate.similarity_score), 0.0)

            counts[pid] = counts.get(pid, 0) + 1
            sims[pid] = sims.get(pid, 0.0) + sim
            first_index.setdefault(pid, index)
            total_sim += sim

        # Tie-breaker for winner: highest count, then earliest occurrence
        consensus_winner = min(counts, key=lambda pid: (-counts[pid], first_index[pid]))
        n = len(candidates)

        identity_count = counts[consensus_winner] / n if n else 0.0
        identity_weighted = sims[consensus_winner] / total_sim if total_sim > 0.0 else 0.0

        ordered_sims = sorted(
            (max(float(c.similarity_score), 0.0) for c in candidates),
            reverse=True,
        )
        margin = ordered_sims[0] - ordered_sims[1] if len(ordered_sims) > 1 else ordered_sims[0]

        count_signal = self._scale(
            identity_count, self._retrieval_consensus_start, self._retrieval_consensus_strong
        )
        weighted_signal = self._scale(
            identity_weighted, self._retrieval_consensus_start, self._retrieval_consensus_strong
        )
        margin_signal = self._scale(
            margin, self._margin_consensus_start, self._margin_consensus_strong
        )

        if not self._retrieval_protection_enabled:
            protection_strength = 0.0
            consensus_agreement = 1.0
        elif self._consensus_mode == "count":
            protection_strength = max(count_signal, margin_signal)
            consensus_agreement = 1.0
        elif self._consensus_mode == "weighted":
            protection_strength = max(weighted_signal, margin_signal)
            consensus_agreement = 1.0
        else:
            # Hybrid mode: Reduce protection up to 30% when count and weighted signals disagree
            consensus_agreement = max(0.0, min(1.0, 1.0 - abs(count_signal - weighted_signal)))
            weighted_consensus = (
                self._retrieval_hybrid_count_weight * count_signal
                + self._retrieval_hybrid_weighted_weight * weighted_signal
                + self._retrieval_hybrid_margin_weight * margin_signal
            )
            agreement_factor = 0.70 + 0.30 * consensus_agreement
            protection_strength = weighted_consensus * agreement_factor

        protection_strength = max(0.0, min(1.0, protection_strength))

        return {
            "enabled": self._retrieval_protection_enabled,
            "available": True,
            "consensus_winner": consensus_winner,
            "consensus": float(identity_count),
            "identity_count_consensus": float(identity_count),
            "identity_weighted_consensus": float(identity_weighted),
            "margin_consensus": float(margin),
            "count_signal": float(count_signal),
            "weighted_signal": float(weighted_signal),
            "margin_signal": float(margin_signal),
            "consensus_agreement": float(consensus_agreement),
            "protection_strength": float(protection_strength),
            "candidate_count": float(n),
        }

    def _plugin_boost_ratio(
        self,
        product_id: str,
        retrieval_guard: dict,
        plugin_name: str,
    ) -> float:
        """Return retrieval protection ratio in [0, 1]."""
        if not self._retrieval_protection_enabled or not retrieval_guard.get("available", False):
            return float(np.clip(self._retrieval_boost_ratio_max, 0.0, 1.0))

        if str(product_id) == retrieval_guard.get("consensus_winner"):
            return float(np.clip(self._retrieval_boost_ratio_max, 0.0, 1.0))

        protection_strength = float(retrieval_guard.get("protection_strength", 0.0))
        strong_ratios = {
            "color": self._retrieval_color_strong_ratio,
            "ocr": self._retrieval_ocr_strong_ratio,
            "barcode": self._retrieval_barcode_strong_ratio,
        }

        start_ratio = float(np.clip(self._retrieval_boost_ratio_max, 0.0, 1.0))
        end_ratio = float(
            np.clip(
                strong_ratios.get(plugin_name, self._retrieval_boost_ratio_min),
                0.0,
                1.0,
            )
        )

        ratio = start_ratio - protection_strength * (start_ratio - end_ratio)
        ratio = max(ratio, float(np.clip(self._retrieval_boost_ratio_min, 0.0, 1.0)))

        return float(np.clip(ratio, 0.0, 1.0))

    # ----------------------------------------------------------------------
    # CONFUSABLE PAIRS
    # ----------------------------------------------------------------------

    def _confusable_pair_for(self, product_id: str) -> frozenset[str] | None:
        """Return configured confusable pair containing product."""
        pid = str(product_id)
        for pair in self._confusable_pairs:
            if pid in pair:
                return pair
        return None

    def _confusable_pair_conflict(
        self,
        confusable_pair: frozenset[str],
        winner: RetrievalCandidate,
        winner_evidence: dict[str, Any],
        scored: list[tuple[float, RetrievalCandidate, dict[str, Any]]],
    ) -> tuple[bool, dict[str, Any] | None]:
        """Detect active disagreement from a competing confusable-pair member.

        Returns (has_conflict, opponent_evidence). A conflict exists only
        when the OTHER member of the pair is present among this crop's
        scored retrieval candidates AND has strictly stronger match
        strength than the winner on at least one plugin (OCR, color, or
        barcode). Absence of evidence on both sides is never treated as
        a conflict — silence should not downgrade an otherwise-accepted
        decision.
        """
        other_id = next(iter(confusable_pair - {str(winner.product_id)}), None)
        if other_id is None:
            return False, None

        opponent_entry = next(
            (ev for _, c, ev in scored if str(c.product_id) == other_id), None
        )
        if opponent_entry is None:
            # Opponent wasn't even among this crop's retrieval candidates —
            # there's nothing to disagree with.
            return False, None

        for key in ("ocr_match_strength", "color_match_strength", "barcode_match_strength"):
            if float(opponent_entry.get(key, 0.0)) > float(winner_evidence.get(key, 0.0)):
                return True, opponent_entry

        return False, opponent_entry

    def _eligible_plugin_count(self, product_id: str, barcode_matches: set[str]) -> int:
        """Count plugins structurally capable of producing evidence for this crop.

        Barcode is only counted as eligible when the barcode plugin
        actually decoded something for this crop (`barcode_matches` is
        non-empty) — previously this checked only whether the catalog
        entry had a `barcode` field, which stayed true even while the
        barcode plugin was globally disabled (`plugins.barcode.enabled:
        false`), silently inflating the required-agreement count.
        """
        count = 1  # OCR is eligible once plugins ran.
        product = self._product_lookup(product_id)

        if barcode_matches and product and str(product.get("barcode", "")).strip():
            count += 1

        if self._extract_product_color_code(product_id) is not None:
            count += 1

        return count

    # ----------------------------------------------------------------------
    # COLOR MATCHING & CIEDE2000
    # ----------------------------------------------------------------------

    def _load_color_reference(self, path: Path) -> dict[str, list[float]]:
        """Load RGB references and convert them to OpenCV Lab."""
        if not path.exists():
            logger.warning(
                "Color reference file not found: '%s'. Color reranking disabled.", path
            )
            return {}

        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as exc:
            logger.warning("Failed to load color reference '%s': %s", path, exc)
            return {}

        if not isinstance(data, dict):
            logger.warning("Invalid color reference format: expected JSON object.")
            return {}

        result: dict[str, list[float]] = {}
        for code, entry in data.items():
            if not isinstance(entry, dict):
                continue

            rgb = entry.get("rgb")
            if not isinstance(rgb, (list, tuple)) or len(rgb) != 3:
                continue

            try:
                r, g, b = float(rgb[0]), float(rgb[1]), float(rgb[2])
            except (TypeError, ValueError):
                continue

            if not all(np.isfinite(v) for v in (r, g, b)) or not all(
                0.0 <= v <= 255.0 for v in (r, g, b)
            ):
                continue

            bgr = np.array([[[b, g, r]]], dtype=np.uint8)
            lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)[0, 0]
            normalized_code = str(code).upper().strip()

            if normalized_code:
                result[normalized_code] = [float(lab[0]), float(lab[1]), float(lab[2])]

        logger.info("Loaded %d color references from '%s'.", len(result), path)
        return result

    @staticmethod
    def _extract_color_lab(plugin_result: PluginResult) -> list[float] | None:
        """Extract representative Lab color from plugin evidence."""
        color_evidence = plugin_result.evidence.get("color")
        if not color_evidence:
            return None

        lab = color_evidence.get("representative_lab")
        if not isinstance(lab, (list, tuple)) or len(lab) != 3:
            return None

        try:
            values = [float(lab[0]), float(lab[1]), float(lab[2])]
        except (TypeError, ValueError):
            return None

        return values if all(np.isfinite(v) for v in values) else None

    @staticmethod
    def _apply_l_dampening(lab: list[float], l_weight: float) -> list[float]:
        """Compress L toward OpenCV Lab neutral center."""
        l_center = 128.0
        dampened_l = l_center + (float(lab[0]) - l_center) * l_weight
        return [dampened_l, float(lab[1]), float(lab[2])]

    def _calculate_color_distances(
        self,
        candidates: list[RetrievalCandidate],
        query_lab: list[float] | None,
    ) -> dict[str, float]:
        """Calculate query-color -> catalog-color CIEDE2000."""
        if not self._color_enabled or query_lab is None or not self._color_reference:
            return {}

        l_weight = float(self._color_l_weight)
        query_lab_dampened = self._apply_l_dampening(query_lab, l_weight)
        distances: dict[str, float] = {}

        for candidate in candidates:
            product_id = str(candidate.product_id)
            color_code = self._extract_product_color_code(product_id)
            if not color_code:
                continue

            reference_lab = self._resolve_color_reference(color_code)
            if reference_lab is None:
                continue

            reference_lab_dampened = self._apply_l_dampening(reference_lab, l_weight)

            try:
                delta_e = self._delta_e_2000(query_lab_dampened, reference_lab_dampened)
            except Exception as exc:
                logger.warning(
                    "Failed color distance calculation for product_id='%s' color_code='%s': %s",
                    product_id,
                    color_code,
                    exc,
                )
                continue

            distances[product_id] = float(delta_e)
            logger.debug(
                "Color distance: product_id='%s' code='%s' ΔE=%.3f (l_weight=%.2f)",
                product_id,
                color_code,
                delta_e,
                l_weight,
            )

        return distances

    def _resolve_color_reference(self, color_code: str) -> list[float] | None:
        """Resolve a color code to Lab reference."""
        code = str(color_code).upper().strip()
        if not code:
            return None

        reference = self._color_reference.get(code)
        if reference is not None:
            return reference

        if self._color_reference_lookup is None:
            return None

        try:
            reference = self._color_reference_lookup(code)
        except Exception as exc:
            logger.warning("Color reference lookup failed for code='%s': %s", code, exc)
            return None

        if reference is None:
            return None

        try:
            values = [float(reference[0]), float(reference[1]), float(reference[2])]
            return values if all(np.isfinite(v) for v in values) else None
        except (TypeError, ValueError, IndexError):
            return None

    def _extract_product_color_code(self, product_id: str) -> str | None:
        """Extract color/variant code from product name."""
        product_id = str(product_id)
        if product_id in self._product_color_code_cache:
            return self._product_color_code_cache[product_id]

        product = self._product_lookup(product_id)
        product_name = str(product.get("product_name", "")).strip() if product else ""

        if not product_name:
            self._product_color_code_cache[product_id] = None
            return None

        code = self._extract_color_code_from_name(product_name, self._color_reference)
        self._product_color_code_cache[product_id] = code

        if code:
            logger.debug(
                "Product color code inferred: product_id='%s' name='%s' -> code='%s'",
                product_id,
                product_name,
                code,
            )

        return code

    @staticmethod
    def _extract_color_code_from_name(
        product_name: str,
        known_codes: dict[str, list[float]] | None = None,
    ) -> str | None:
        """Infer color/variant identifier from product name."""
        if not product_name:
            return None

        text = str(product_name).upper().strip()

        # 1. Bracketed codes
        bracket_groups = re.findall(r"[\(\（\[\【]([A-Z0-9_-]{2,20})[\)\）\]\】]", text)
        for value in reversed(bracket_groups):
            compact = re.sub(r"[^A-Z0-9]", "", value)
            if re.fullmatch(r"[A-Z]{2,6}\d{2,6}", compact):
                return compact

        if known_codes:
            for value in reversed(bracket_groups):
                compact = re.sub(r"[^A-Z0-9]", "", value)
                if compact in known_codes:
                    return compact

        # 2. Standard alphanumeric code
        matches = re.findall(r"\b[A-Z]{2,6}\d{2,6}\b", text)
        if matches:
            if known_codes:
                for match in reversed(matches):
                    if match in known_codes:
                        return match
            return matches[-1]

        # 3. Pure alphabetic variant code
        if known_codes:
            for match in reversed(re.findall(r"\b[A-Z]{3,6}\b", text)):
                if match in known_codes:
                    return match

        return None

    def _build_color_context(self, distances: dict[str, float]) -> dict[str, float | bool]:
        """Build color comparison context."""
        if not distances:
            return {
                "available": False,
                "best_delta_e": 0.0,
                "second_delta_e": 0.0,
                "margin": 0.0,
            }

        ordered = sorted(distances.values())
        best = ordered[0]
        second = ordered[1] if len(ordered) > 1 else float("inf")
        margin = second - best if np.isfinite(second) else best

        return {
            "available": True,
            "best_delta_e": float(best),
            "second_delta_e": float(second),
            "margin": float(margin),
        }

    def _color_match_strength(
        self,
        product_id: str,
        distances: dict[str, float],
        context: dict[str, float | bool],
    ) -> float:
        """Return normalized color match strength in [0, 1]."""
        if not distances or product_id not in distances:
            return 0.0

        delta_e = float(distances[product_id])
        if delta_e > self._color_delta_e_weak:
            return 0.0

        if delta_e <= self._color_delta_e_strong:
            distance_score = 1.0
        else:
            denominator = self._color_delta_e_weak - self._color_delta_e_strong
            distance_score = float(
                np.clip((self._color_delta_e_weak - delta_e) / max(denominator, 1e-6), 0.0, 1.0)
            )

        if product_id != self._best_color_product_id(distances):
            return 0.0

        margin = float(context.get("margin", 0.0))
        if margin <= self._color_min_margin:
            margin_score = 0.0
        else:
            margin_score = float(
                np.clip((margin - self._color_min_margin) / max(self._color_margin_scale, 1e-6), 0.0, 1.0)
            )

        return float(distance_score * margin_score)

    @staticmethod
    def _best_color_product_id(distances: dict[str, float]) -> str | None:
        """Return candidate with smallest color distance."""
        return min(distances, key=distances.get) if distances else None

    @staticmethod
    def _delta_e_2000(lab1: list[float], lab2: list[float]) -> float:
        """Calculate CIEDE2000 between two OpenCV Lab colors."""

        def opencv_to_cielab(lab: list[float]) -> tuple[float, float, float]:
            return (
                float(lab[0]) * 100.0 / 255.0,
                float(lab[1]) - 128.0,
                float(lab[2]) - 128.0,
            )

        L1, a1, b1 = opencv_to_cielab(lab1)
        L2, a2, b2 = opencv_to_cielab(lab2)

        C1 = np.sqrt(a1 * a1 + b1 * b1)
        C2 = np.sqrt(a2 * a2 + b2 * b2)
        C_bar = (C1 + C2) / 2.0

        G = 0.5 * (1.0 - np.sqrt(C_bar**7 / (C_bar**7 + 25.0**7)))

        a1_prime = (1.0 + G) * a1
        a2_prime = (1.0 + G) * a2

        C1_prime = np.sqrt(a1_prime**2 + b1**2)
        C2_prime = np.sqrt(a2_prime**2 + b2**2)

        def hue_angle(a: float, b: float) -> float:
            angle = np.degrees(np.arctan2(b, a))
            return angle + 360.0 if angle < 0 else angle

        h1_prime = hue_angle(a1_prime, b1)
        h2_prime = hue_angle(a2_prime, b2)

        delta_L_prime = L2 - L1
        delta_C_prime = C2_prime - C1_prime

        if C1_prime * C2_prime == 0:
            delta_h_prime = 0.0
        elif abs(h2_prime - h1_prime) <= 180.0:
            delta_h_prime = h2_prime - h1_prime
        elif (h2_prime - h1_prime) > 180.0:
            delta_h_prime = h2_prime - h1_prime - 360.0
        else:
            delta_h_prime = h2_prime - h1_prime + 360.0

        delta_H_prime = (
            2.0 * np.sqrt(C1_prime * C2_prime) * np.sin(np.radians(delta_h_prime / 2.0))
        )

        L_bar_prime = (L1 + L2) / 2.0
        C_bar_prime = (C1_prime + C2_prime) / 2.0

        if C1_prime * C2_prime == 0:
            h_bar_prime = h1_prime + h2_prime
        elif abs(h1_prime - h2_prime) <= 180.0:
            h_bar_prime = (h1_prime + h2_prime) / 2.0
        elif (h1_prime + h2_prime) < 360.0:
            h_bar_prime = (h1_prime + h2_prime + 360.0) / 2.0
        else:
            h_bar_prime = (h1_prime + h2_prime - 360.0) / 2.0

        T = (
            1.0
            - 0.17 * np.cos(np.radians(h_bar_prime - 30.0))
            + 0.24 * np.cos(np.radians(2.0 * h_bar_prime))
            + 0.32 * np.cos(np.radians(3.0 * h_bar_prime + 6.0))
            - 0.20 * np.cos(np.radians(4.0 * h_bar_prime - 63.0))
        )

        delta_theta = 30.0 * np.exp(-(((h_bar_prime - 275.0) / 25.0) ** 2))
        R_C = 2.0 * np.sqrt(C_bar_prime**7 / (C_bar_prime**7 + 25.0**7))

        S_L = 1.0 + (
            0.015 * (L_bar_prime - 50.0) ** 2 / np.sqrt(20.0 + (L_bar_prime - 50.0) ** 2)
        )
        S_C = 1.0 + 0.045 * C_bar_prime
        S_H = 1.0 + 0.015 * C_bar_prime * T
        R_T = -np.sin(np.radians(2.0 * delta_theta)) * R_C

        delta_E = np.sqrt(
            (delta_L_prime / S_L) ** 2
            + (delta_C_prime / S_C) ** 2
            + (delta_H_prime / S_H) ** 2
            + R_T * (delta_C_prime / S_C) * (delta_H_prime / S_H)
        )

        return float(delta_E)

    # ----------------------------------------------------------------------
    # BARCODE MATCH LOGIC
    # ----------------------------------------------------------------------

    @staticmethod
    def _normalize_barcode(value: str) -> str:
        """Normalize a barcode value for comparison.

        Strips non-digit characters and aligns UPC-A (12 digits) to
        EAN-13/JAN (13 digits) by prepending a leading zero, since the
        two encode the same identifier and catalog/decoder data commonly
        mixes the two forms.
        """
        digits = re.sub(r"[^0-9]", "", str(value))
        if len(digits) == 12:
            digits = "0" + digits
        return digits

    @classmethod
    def _extract_barcode_matches(cls, plugin_result: PluginResult) -> set[str]:
        """Extract decoded barcode values, normalized for comparison."""
        barcode_evidence = plugin_result.evidence.get("barcode")
        if not barcode_evidence:
            return set()

        matches: set[str] = set()
        for entry in barcode_evidence.get("barcodes", []):
            raw = entry.get("data")
            if not raw:
                continue
            normalized = cls._normalize_barcode(raw)
            if normalized:
                matches.add(normalized)

        return matches

    def _barcode_match_strength(
        self,
        product_id: str,
        barcode_matches: set[str],
    ) -> float:
        """Return exact barcode match strength (after normalization)."""
        if not barcode_matches:
            return 0.0

        product = self._product_lookup(product_id)
        if not product:
            return 0.0

        catalog_barcode = self._normalize_barcode(product.get("barcode", ""))
        return 1.0 if (catalog_barcode and catalog_barcode in barcode_matches) else 0.0

    # ----------------------------------------------------------------------
    # GENERIC HELPERS
    # ----------------------------------------------------------------------

    @staticmethod
    def _normalize_text(text: str) -> str:
        """Normalize OCR/catalog text for matching."""
        return "".join(char for char in text.upper().strip() if char.isalnum())

    @staticmethod
    def _extract_generic_boost(plugin_result: PluginResult, exclude: set[str]) -> float:
        """Legacy helper for backward compatibility."""
        total = 0.0
        for plugin_name, evidence in plugin_result.evidence.items():
            if plugin_name in exclude:
                continue
            total += float(evidence.get("confidence_boost", 0.0))
        return total

    @staticmethod
    def _safe_float(value: Any, default: float = 0.0) -> float:
        """Safely convert a value to finite float."""
        try:
            converted = float(value)
            if np.isfinite(converted):
                return converted
        except (TypeError, ValueError):
            pass
        return default

    @staticmethod
    def _safe_int(value: Any, default: int = 0) -> int:
        """Safely convert a value to int."""
        try:
            return int(value)
        except (TypeError, ValueError):
            return default