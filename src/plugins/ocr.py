from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set

import cv2
import numpy as np

from src.core.config import AppConfig
from src.core.logger import get_logger
from src.core.utils import timer
from src.models.models import CropImage

logger = get_logger(__name__)


class OcrPlugin:
    """Extract discriminative packaging text via EasyOCR."""

    name = "ocr"

    # Information Scoring
    INFORMATION_LENGTH_NORMALIZER = 16.0
    INFORMATION_FRAGMENT_NORMALIZER = 5.0
    INFORMATION_SCORE_NORMALIZER = 2.5

    # Text-Type Weights
    TYPE_WEIGHTS = {
        "alphabetic": 1.00,
        "mixed": 0.82,
        "numeric": 0.42,
        "other": 0.20,
    }

    # Orientation Score
    INFORMATION_WEIGHT = 0.45
    TEXT_AMOUNT_WEIGHT = 0.20
    FRAGMENT_WEIGHT = 0.15
    CONFIDENCE_WEIGHT = 0.12
    QUALITY_WEIGHT = 0.08

    # Weak OCR fragments penalty
    WEAK_FRAGMENT_PENALTY = 0.15
    MIN_WEAK_PENALTY_FACTOR = 0.70

    # OCR Confidence
    NON_USEFUL_CONFIDENCE_FACTOR = 0.20
    MIN_CONFIDENCE_WEIGHT = 0.05

    # Fragment Protection
    SINGLE_FRAGMENT_CAP = 0.30
    SHORT_NUMERIC_FRAGMENT_CAP = 0.35

    # Orientation Protection
    MIN_MEANINGFUL_CHARS_FOR_STRONG_ORIENTATION = 3
    STRONG_ORIENTATION_MIN_SCORE = 0.20

    # Fragment Quality
    QUALITY_LENGTH_NORMALIZER = 8.0
    HIGH_SYMBOL_RATIO = 0.40
    HIGH_SYMBOL_PENALTY = 0.70

    # Orientation Ambiguity
    ORIENTATION_AMBIGUITY_MARGIN = 0.1
    MAX_ORIENTATION_CANDIDATES = 2
    MIN_AMBIGUOUS_INFORMATION_SCORE = 0.05
    MIN_AMBIGUOUS_USEFUL_CHARS = 3

    # ------------------------------------------------------------------
    # INITIALIZATION & SETUP
    # ------------------------------------------------------------------

    def __init__(self, config: AppConfig) -> None:
        """Initialize the OCR plugin and load EasyOCR once."""
        self._config = config.plugins.ocr
        self._reader = self._load_reader() if self._config.enabled else None

        logger.info(
            "OcrPlugin initialized (enabled=%s language=%s device=%s)",
            self._config.enabled,
            self._languages(),
            self._config.device,
        )

    def _languages(self) -> List[str]:
        """Normalize configured language value to a list."""
        language = self._config.language
        return [str(v) for v in language] if isinstance(language, list) else [str(language)]

    def _load_reader(self) -> Any:
        """Load EasyOCR reader exactly once."""
        try:
            import easyocr
        except ImportError as exc:
            raise ImportError(
                "OCR plugin requires optional dependency 'easyocr'. "
                "Install it with: pip install easyocr"
            ) from exc

        device = str(self._config.device).lower().strip()
        return easyocr.Reader(self._languages(), gpu=(device == "cuda"))

    # ------------------------------------------------------------------
    # PUBLIC API
    # ------------------------------------------------------------------

    def is_enabled(self) -> bool:
        """Return whether OCR is enabled."""
        return bool(self._config.enabled)

    def run(self, crop: CropImage) -> Dict[str, Any]:
        """Extract useful discriminative text from a product crop."""
        if not self.is_enabled() or self._reader is None:
            return self._empty_result()

        with timer() as elapsed:
            image = self._validate_image(crop.raw_image_array)
            if image is None:
                logger.warning("OCR received invalid image for crop_id='%s'", crop.crop_id)
                return self._empty_result()

            processed = self._preprocess(image)
            orientation_results: List[Dict[str, Any]] = []

            for rotation in self._get_rotations():
                rotated = self._rotate(processed, rotation)
                result = self._run_ocr(rotated, rotation)
                result["score"] = self._score_result(result)
                orientation_results.append(result)

                logger.debug(
                    "OCR orientation=%d score=%.4f confidence=%.4f "
                    "information=%.4f max=%.4f mean=%.4f useful_chars=%d "
                    "useful_fragments=%d fragments=%d text='%s' crop_id='%s'",
                    rotation,
                    result["score"],
                    result["confidence"],
                    result["information_score"],
                    result["max_fragment_confidence"],
                    result["mean_fragment_confidence"],
                    result["useful_text_length"],
                    result["useful_fragment_count"],
                    len(result["fragments"]),
                    result["text"],
                    crop.crop_id,
                )

            orientation_candidates = self._select_orientation_candidates(orientation_results)
            best_result = orientation_candidates[0] if orientation_candidates else self._empty_orientation_result()

            fragments = best_result.get("fragments", [])
            text = self._merge_fragments(fragments)

        logger.info(
            "OcrPlugin extracted text='%s' rotation=%d score=%.4f "
            "confidence=%.4f information=%.4f max_conf=%.4f "
            "mean_conf=%.4f fragments=%d useful_fragments=%d chars=%d "
            "orientation_candidates=%d ambiguous=%s crop_id='%s' (%.2f ms)",
            text,
            int(best_result.get("rotation", 0)),
            float(best_result.get("score", 0.0)),
            float(best_result.get("confidence", 0.0)),
            float(best_result.get("information_score", 0.0)),
            float(best_result.get("max_fragment_confidence", 0.0)),
            float(best_result.get("mean_fragment_confidence", 0.0)),
            len(fragments),
            int(best_result.get("useful_fragment_count", 0)),
            len(text),
            len(orientation_candidates),
            len(orientation_candidates) > 1,
            crop.crop_id,
            elapsed["elapsed_ms"],
        )

        output = self._build_output(
            text=text,
            best_result=best_result,
            orientation_results=orientation_results,
            orientation_candidates=orientation_candidates,
        )
        output["latency_ms"] = elapsed["elapsed_ms"]
        return output

    # ------------------------------------------------------------------
    # OUTPUT BUILDERS
    # ------------------------------------------------------------------

    def _build_output(
        self,
        text: str,
        best_result: Dict[str, Any],
        orientation_results: List[Dict[str, Any]],
        orientation_candidates: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Build public OCR result and preserve useful diagnostics."""
        fragments = best_result.get("fragments", [])
        rotation = best_result.get("rotation", 0)

        return {
            "text": text,
            "text_length": len(text),
            "confidence": float(best_result.get("confidence", 0.0)),
            "max_fragment_confidence": float(best_result.get("max_fragment_confidence", 0.0)),
            "mean_fragment_confidence": float(best_result.get("mean_fragment_confidence", 0.0)),
            "fragments": fragments,
            "rotation": int(rotation),
            "score": float(best_result.get("score", 0.0)),
            "information_score": float(best_result.get("information_score", 0.0)),
            "useful_fragment_count": int(best_result.get("useful_fragment_count", 0)),
            "useful_text_length": int(best_result.get("useful_text_length", 0)),
            "image_shape": best_result.get("image_shape", []),
            "ocr_boxes": [self._build_fragment_output(f, rotation) for f in fragments],
            "orientation_scores": [self._build_orientation_diagnostic(r) for r in orientation_results],
            "orientation_candidates": [self._build_orientation_candidate(r) for r in orientation_candidates],
            "orientation_ambiguous": len(orientation_candidates) > 1,
            "orientation_candidate_count": len(orientation_candidates),
        }

    def _build_fragment_output(self, fragment: Dict[str, Any], default_rotation: int) -> Dict[str, Any]:
        """Build a compact OCR fragment representation."""
        return {
            "text": fragment.get("text", ""),
            "confidence": float(fragment.get("confidence", 0.0)),
            "bbox": fragment.get("bbox", []),
            "rotation": int(fragment.get("rotation", default_rotation)),
            "quality": float(fragment.get("quality", 0.0)),
            "useful": bool(fragment.get("useful", False)),
            "text_type": fragment.get("text_type", "other"),
            "text_length": int(fragment.get("text_length", 0)),
            "alnum_length": int(fragment.get("alnum_length", 0)),
        }

    def _build_orientation_candidate(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """Build an orientation candidate for downstream reranking."""
        rotation = result.get("rotation", 0)
        return {
            "rotation": int(rotation),
            "score": float(result.get("score", 0.0)),
            "confidence": float(result.get("confidence", 0.0)),
            "max_fragment_confidence": float(result.get("max_fragment_confidence", 0.0)),
            "mean_fragment_confidence": float(result.get("mean_fragment_confidence", 0.0)),
            "information_score": float(result.get("information_score", 0.0)),
            "text": result.get("text", ""),
            "text_length": int(result.get("text_length", 0)),
            "useful_text_length": int(result.get("useful_text_length", 0)),
            "useful_fragment_count": int(result.get("useful_fragment_count", 0)),
            "fragments": [self._build_fragment_output(f, rotation) for f in result.get("fragments", [])],
        }

    def _build_orientation_diagnostic(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """Build a compact diagnostic representation."""
        rotation = result.get("rotation", 0)
        return {
            "rotation": int(rotation),
            "score": float(result.get("score", 0.0)),
            "confidence": float(result.get("confidence", 0.0)),
            "information_score": float(result.get("information_score", 0.0)),
            "text": result.get("text", ""),
            "text_length": int(result.get("text_length", 0)),
            "useful_text_length": int(result.get("useful_text_length", 0)),
            "useful_fragment_count": int(result.get("useful_fragment_count", 0)),
            "fragment_count": len(result.get("fragments", [])),
            "max_confidence": float(result.get("max_fragment_confidence", 0.0)),
            "mean_confidence": float(result.get("mean_fragment_confidence", 0.0)),
            "image_shape": result.get("image_shape", []),
            "fragments": [self._build_fragment_output(f, rotation) for f in result.get("fragments", [])],
        }

    # ------------------------------------------------------------------
    # IMAGE PROCESSING & TRANSFORMATION
    # ------------------------------------------------------------------

    def _validate_image(self, image: Any) -> Optional[np.ndarray]:
        """Validate input image array."""
        if image is None or not isinstance(image, np.ndarray) or image.size == 0:
            return None
        if image.ndim not in (2, 3) or image.shape[0] < 2 or image.shape[1] < 2:
            return None
        return image if np.all(np.isfinite(image)) else None

    def _preprocess(self, image: np.ndarray) -> np.ndarray:
        """Preprocess image using adaptive upscale and CLAHE."""
        scale = self._get_upscale_factor(image)
        if scale > 1:
            image = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)

        if image.ndim == 2:
            image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)

        lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
        l_channel, a_channel, b_channel = cv2.split(lab)

        clahe = cv2.createCLAHE(
            clipLimit=float(self._config.clahe_clip_limit),
            tileGridSize=(int(self._config.clahe_tile_grid_size), int(self._config.clahe_tile_grid_size)),
        )
        l_channel = clahe.apply(l_channel)
        lab = cv2.merge((l_channel, a_channel, b_channel))
        return cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)

    def _get_upscale_factor(self, image: np.ndarray) -> int:
        """Choose upscale factor from configuration."""
        if not self._config.upscale_enabled:
            return 1

        shortest_side = min(image.shape[:2])
        if shortest_side < self._config.upscale_threshold_small:
            return min(3, int(self._config.upscale_max))
        if shortest_side < self._config.upscale_threshold_medium:
            return min(2, int(self._config.upscale_max))
        return 1

    def _get_rotations(self) -> List[int]:
        """Return all configured OCR orientations."""
        rotations = [0]
        if not self._config.rotation_enabled:
            return rotations

        for angle in self._config.rotation_angles:
            try:
                angle_deg = int(angle) % 360
                if angle_deg not in rotations:
                    rotations.append(angle_deg)
            except (TypeError, ValueError):
                continue
        return rotations

    def _rotate(self, image: np.ndarray, angle: int) -> np.ndarray:
        """Rotate image by a multiple of 90 degrees."""
        angle_deg = int(angle) % 360
        if angle_deg == 90:
            return cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE)
        if angle_deg == 180:
            return cv2.rotate(image, cv2.ROTATE_180)
        if angle_deg == 270:
            return cv2.rotate(image, cv2.ROTATE_90_COUNTERCLOCKWISE)
        return image

    # ------------------------------------------------------------------
    # OCR ENGINE & EVALUATION
    # ------------------------------------------------------------------

    def _run_ocr(self, image: np.ndarray, rotation: int) -> Dict[str, Any]:
        """Run EasyOCR and evaluate every OCR fragment."""
        try:
            results = self._reader.readtext(image, detail=1, paragraph=False)
        except Exception:
            logger.exception("EasyOCR failed during rotation=%d", rotation)
            return self._empty_orientation_result(rotation=rotation, image_shape=image.shape)

        fragments: List[Dict[str, Any]] = []

        for result in results:
            if not isinstance(result, (list, tuple)) or len(result) < 3:
                continue

            bbox, text_raw, confidence_raw = result[:3]
            try:
                confidence = float(confidence_raw)
            except (TypeError, ValueError):
                continue

            if not np.isfinite(confidence):
                continue

            confidence = float(np.clip(confidence, 0.0, 1.0))
            text = self._normalize_text(str(text_raw))
            if not text:
                continue

            normalized_bbox = self._normalize_bbox(bbox)
            if not normalized_bbox:
                continue

            quality = self._fragment_quality(text)
            fragments.append({
                "text": text,
                "confidence": confidence,
                "bbox": normalized_bbox,
                "rotation": int(rotation),
                "quality": quality["quality"],
                "useful": quality["useful"],
                "text_length": quality["length"],
                "alnum_length": quality["alnum_length"],
                "text_type": quality["text_type"],
            })

        text = self._merge_fragments(fragments)
        confidences = [float(f["confidence"]) for f in fragments]
        max_confidence = max(confidences, default=0.0)
        mean_confidence = float(np.mean(confidences)) if confidences else 0.0

        useful_fragments = [f for f in fragments if f.get("useful", False)]
        useful_text_length = sum(int(f.get("alnum_length", 0)) for f in useful_fragments)

        return {
            "fragments": fragments,
            "confidence": self._calculate_ocr_confidence(useful_fragments, mean_confidence),
            "max_fragment_confidence": max_confidence,
            "mean_fragment_confidence": mean_confidence,
            "text_length": len(text),
            "useful_text_length": useful_text_length,
            "useful_fragment_count": len(useful_fragments),
            "information_score": self._calculate_information_score(useful_fragments),
            "text": text,
            "rotation": int(rotation),
            "image_shape": list(image.shape[:2]),
            "score": 0.0,
        }

    def _calculate_ocr_confidence(self, useful_fragments: List[Dict[str, Any]], mean_confidence: float) -> float:
        """Calculate OCR evidence quality."""
        if not useful_fragments:
            return float(np.clip(mean_confidence * self.NON_USEFUL_CONFIDENCE_FACTOR, 0.0, 1.0))

        weighted_sum = 0.0
        weight_sum = 0.0

        for fragment in useful_fragments:
            confidence = float(fragment.get("confidence", 0.0))
            quality = float(fragment.get("quality", 0.0))
            weight = max(quality, self.MIN_CONFIDENCE_WEIGHT)

            weighted_sum += confidence * weight
            weight_sum += weight

        return float(np.clip(weighted_sum / weight_sum, 0.0, 1.0)) if weight_sum > 0.0 else 0.0

    def _normalize_bbox(self, bbox: Any) -> List[List[float]]:
        """Convert EasyOCR bbox to JSON-friendly 4-point format."""
        if bbox is None:
            return []

        try:
            array = np.asarray(bbox, dtype=np.float32)
            if array.ndim != 2 or array.shape != (4, 2) or not np.all(np.isfinite(array)):
                return []
            return array.tolist()
        except Exception:
            logger.debug("Unable to normalize OCR bbox: %r", bbox)
            return []

    def _fragment_quality(self, text: str) -> Dict[str, Any]:
        """Evaluate OCR fragment usefulness and intrinsic quality."""
        normalized = self._normalize_text(text)
        if not normalized:
            return {"quality": 0.0, "useful": False, "length": 0, "alnum_length": 0, "text_type": "other"}

        alnum = re.sub(r"[^A-Za-z0-9À-ỹぁ-んァ-ヶ一-龯]", "", normalized)
        alnum_length = len(alnum)

        has_alpha = any(c.isalpha() for c in alnum)
        has_digit = any(c.isdigit() for c in alnum)

        if has_alpha and has_digit:
            text_type = "mixed"
        elif has_alpha:
            text_type = "alphabetic"
        elif has_digit:
            text_type = "numeric"
        else:
            text_type = "other"

        if alnum_length <= 1:
            return {"quality": 0.02, "useful": False, "length": len(normalized), "alnum_length": alnum_length, "text_type": text_type}
        if alnum_length == 2:
            return {"quality": 0.15, "useful": False, "length": len(normalized), "alnum_length": alnum_length, "text_type": text_type}

        length_quality = min(alnum_length / self.QUALITY_LENGTH_NORMALIZER, 1.0)
        type_weight = self.TYPE_WEIGHTS.get(text_type, self.TYPE_WEIGHTS["other"])
        quality = length_quality * type_weight

        symbol_count = sum(not c.isalnum() for c in normalized)
        if (symbol_count / len(normalized) if normalized else 0.0) > self.HIGH_SYMBOL_RATIO:
            quality *= self.HIGH_SYMBOL_PENALTY

        useful = alnum_length >= 3 and text_type != "other"

        return {
            "quality": float(np.clip(quality, 0.0, 1.0)),
            "useful": useful,
            "length": len(normalized),
            "alnum_length": alnum_length,
            "text_type": text_type,
        }

    def _calculate_information_score(self, fragments: List[Dict[str, Any]]) -> float:
        """Calculate text information independently from confidence."""
        if not fragments:
            return 0.0

        total_information = 0.0
        for fragment in fragments:
            length = int(fragment.get("alnum_length", 0))
            if length <= 0:
                continue

            text_type = str(fragment.get("text_type", "other"))
            if length == 1:
                contribution = 0.02
            elif length == 2:
                contribution = 0.08
            else:
                length_score = min(length / self.QUALITY_LENGTH_NORMALIZER, 1.0)
                type_weight = self.TYPE_WEIGHTS.get(text_type, self.TYPE_WEIGHTS["other"])
                contribution = length_score * type_weight

            total_information += contribution

        return float(np.clip(total_information / self.INFORMATION_SCORE_NORMALIZER, 0.0, 1.0))

    # ------------------------------------------------------------------
    # ORIENTATION SCORING & SELECTION
    # ------------------------------------------------------------------

    def _score_result(self, result: Dict[str, Any]) -> float:
        """Calculate orientation quality score used only to rank orientations."""
        fragments = result.get("fragments", [])
        if not fragments:
            return 0.0

        useful_fragments = [f for f in fragments if f.get("useful", False)]
        if not useful_fragments:
            return 0.01

        information_score = float(result.get("information_score", 0.0))
        useful_chars = sum(int(f.get("alnum_length", 0)) for f in useful_fragments)

        text_score = min(useful_chars / self.INFORMATION_LENGTH_NORMALIZER, 1.0)
        useful_fragment_count = len(useful_fragments)
        fragment_score = min(useful_fragment_count / self.INFORMATION_FRAGMENT_NORMALIZER, 1.0)

        mean_useful_confidence = float(np.mean([float(f.get("confidence", 0.0)) for f in useful_fragments]))
        mean_quality = float(np.mean([float(f.get("quality", 0.0)) for f in useful_fragments]))

        weak_fragments = [f for f in fragments if not f.get("useful", False)]
        weak_ratio = len(weak_fragments) / max(len(fragments), 1)
        weak_penalty = max(self.MIN_WEAK_PENALTY_FACTOR, 1.0 - (self.WEAK_FRAGMENT_PENALTY * weak_ratio))

        score = (
            self.INFORMATION_WEIGHT * information_score
            + self.TEXT_AMOUNT_WEIGHT * text_score
            + self.FRAGMENT_WEIGHT * fragment_score
            + self.CONFIDENCE_WEIGHT * mean_useful_confidence
            + self.QUALITY_WEIGHT * mean_quality
        ) * weak_penalty

        if useful_fragment_count == 1 and useful_chars <= 2:
            score = min(score, self.SINGLE_FRAGMENT_CAP)

        if (
            useful_fragment_count == 1
            and useful_chars <= 3
            and useful_fragments[0].get("text_type") == "numeric"
        ):
            score = min(score, self.SHORT_NUMERIC_FRAGMENT_CAP)

        if (
            useful_chars >= self.MIN_MEANINGFUL_CHARS_FOR_STRONG_ORIENTATION
            and information_score >= self.STRONG_ORIENTATION_MIN_SCORE
        ):
            score = max(score, self.QUALITY_WEIGHT * mean_quality)

        return float(np.clip(score, 0.0, 1.0))

    def _select_orientation_candidates(self, results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Return Top-1 and, when ambiguous, Top-2 orientations."""
        candidates = self._get_orientation_candidates(results)
        if not candidates:
            return []

        ranked = sorted(candidates, key=self._orientation_sort_key, reverse=True)
        best = ranked[0]
        if len(ranked) == 1:
            return [best]

        second = ranked[1]
        score_1 = float(best.get("score", 0.0))
        score_2 = float(second.get("score", 0.0))
        score_gap = abs(score_1 - score_2)

        second_has_meaningful_text = int(second.get("useful_text_length", 0)) >= self.MIN_AMBIGUOUS_USEFUL_CHARS
        second_has_information = float(second.get("information_score", 0.0)) >= self.MIN_AMBIGUOUS_INFORMATION_SCORE

        if score_gap <= self.ORIENTATION_AMBIGUITY_MARGIN and second_has_meaningful_text and second_has_information:
            logger.debug(
                "OCR orientation ambiguous: top1_rotation=%d top1_score=%.4f "
                "top2_rotation=%d top2_score=%.4f gap=%.4f",
                int(best.get("rotation", 0)),
                score_1,
                int(second.get("rotation", 0)),
                score_2,
                score_gap,
            )
            return ranked[: self.MAX_ORIENTATION_CANDIDATES]

        return [best]

    def _get_orientation_candidates(self, results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Filter unusable orientation results."""
        if not results:
            return []
        non_empty = [r for r in results if r.get("fragments", [])]
        return non_empty if non_empty else results

    @staticmethod
    def _orientation_sort_key(result: Dict[str, Any]) -> tuple:
        """Stable ordering for orientation results."""
        return (
            float(result.get("score", 0.0)),
            float(result.get("information_score", 0.0)),
            int(result.get("useful_text_length", 0)),
            int(result.get("useful_fragment_count", 0)),
            float(result.get("confidence", 0.0)),
            float(result.get("max_fragment_confidence", 0.0)),
        )

    # ------------------------------------------------------------------
    # UTILITIES & DEFAULTS
    # ------------------------------------------------------------------

    def _confidence_threshold(self) -> float:
        """Return configured OCR confidence threshold."""
        value = getattr(self._config, "confidence_threshold", 0.50)
        try:
            return float(np.clip(float(value), 0.0, 1.0))
        except (TypeError, ValueError):
            return 0.50

    @staticmethod
    def _normalize_text(text: str) -> str:
        """Normalize OCR whitespace without character correction."""
        return " ".join(text.strip().split())

    def _merge_fragments(self, fragments: List[Dict[str, Any]]) -> str:
        """Merge OCR fragments while preserving EasyOCR order."""
        if not fragments:
            return ""

        seen: Set[str] = set()
        texts: List[str] = []

        for fragment in fragments:
            text = self._normalize_text(str(fragment.get("text", "")))
            if not text:
                continue

            key = text.casefold()
            if key in seen:
                continue

            seen.add(key)
            texts.append(text)

        return " ".join(texts)

    def _empty_orientation_result(self, rotation: int = 0, image_shape: Any = None) -> Dict[str, Any]:
        """Return a consistent empty orientation result."""
        shape = list(image_shape[:2]) if isinstance(image_shape, (tuple, list)) else []
        return {
            "fragments": [],
            "confidence": 0.0,
            "max_fragment_confidence": 0.0,
            "mean_fragment_confidence": 0.0,
            "text_length": 0,
            "useful_text_length": 0,
            "useful_fragment_count": 0,
            "information_score": 0.0,
            "text": "",
            "rotation": int(rotation),
            "score": 0.0,
            "image_shape": shape,
        }

    def _empty_result(self) -> Dict[str, Any]:
        """Return a consistent empty OCR result."""
        return {
            "text": "",
            "text_length": 0,
            "confidence": 0.0,
            "max_fragment_confidence": 0.0,
            "mean_fragment_confidence": 0.0,
            "fragments": [],
            "rotation": 0,
            "score": 0.0,
            "information_score": 0.0,
            "useful_fragment_count": 0,
            "useful_text_length": 0,
            "image_shape": [],
            "ocr_boxes": [],
            "orientation_scores": [],
            "orientation_candidates": [],
            "orientation_ambiguous": False,
            "orientation_candidate_count": 0,
        }