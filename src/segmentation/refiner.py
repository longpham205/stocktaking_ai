"""Detection refinement (segmentation) module.

Responsibility: refine the bounding boxes of detections flagged as
suspicious by ``OverlapResolver``, using an optional segmentation model.
This module must NEVER perform detection, retrieval, or product
identification, and the pipeline must never depend on a concrete
segmentation backend (e.g. SAM2) directly — only on this dispatcher (see
config.yaml, "refinement" section header comment).

`Refiner` is a thin dispatcher plus the shared output-validation policy
(`refinement.output.*`): it selects and loads exactly one concrete
`SegmentationBackend` implementation (see `src/segmentation/backends/`)
based on `configs/config.yaml -> refinement.backend`, then applies
coverage/expansion sanity checks to every backend result before handing
it to `Cropper`. New models can be added at any time:

    1. Create `src/segmentation/backends/<name>.py` implementing
       `SegmentationBackend.refine(image_array, bbox, detection_index)`.
    2. Register it with one new branch in `Refiner._load_backend` below.
    3. Point `configs/config.yaml -> refinement.backend` at `<name>`.

Backends currently available:
    - "none" (default): refinement disabled; every RefinedBox is a
      passthrough fallback (`backends/mock_refiner.py`).
    - "sam2": real neural segmentation via SAM2
      (`backends/sam2.py`); requires `pip install torch torchvision sam2`.
"""

from __future__ import annotations

import numpy as np

from src.core.config import AppConfig
from src.core.logger import get_logger
from src.core.utils import timer
from src.models.models import (
    Detection,
    DetectionResult,
    OverlapResult,
    RefinedBox,
    RefinementResult,
)
from src.segmentation.backends.base import SegmentationBackend

logger = get_logger(__name__)


class Refiner:
    """Refines suspicious detection bounding boxes via segmentation."""

    def __init__(self, config: AppConfig) -> None:
        """Initializes the Refiner and loads its backend model exactly once.

        Args:
            config: Fully validated application configuration.
        """
        self._config = config.refinement
        self._output_config = config.refinement.output
        self._backend = self._load_backend()
        logger.info(
            "Refiner initialized with backend='%s' enabled=%s",
            self._config.backend,
            self._config.enabled,
        )

    def _load_backend(self) -> SegmentationBackend | None:
        """Instantiates the configured segmentation backend exactly once.

        Returns:
            An initialized `SegmentationBackend`, or None when
            `refinement.backend == "none"` (refinement fully disabled).

        Raises:
            ValueError: If an unsupported backend name is configured.
            ImportError: If the configured backend's optional
                dependencies are not installed.
        """
        backend_name = self._config.backend

        if backend_name == "none":
            return None

        if backend_name == "mock_refiner":
            from src.segmentation.backends.mock_refiner import MockRefinerBackend

            return MockRefinerBackend()

        if backend_name == "sam2":
            from src.segmentation.backends.sam2 import Sam2Backend

            return Sam2Backend(self._config.sam2)

        raise ValueError(
            f"Unsupported refinement backend: '{backend_name}'. "
            "Available backends: 'none', 'mock_refiner', 'sam2'. To add a "
            "new one, implement src/segmentation/backends/<name>.py and "
            "register it in Refiner._load_backend."
        )

    def refine(
        self,
        image_array: np.ndarray,
        detection_result: DetectionResult,
        overlap_result: OverlapResult,
    ) -> RefinementResult:
        """Refines every detection flagged by OverlapResolver as suspicious.

        Args:
            image_array: BGR pixel array of the full source image.
            detection_result: Original Detector output (never mutated).
            overlap_result: OverlapResolver output indicating which
                detections require refinement.

        Returns:
            A RefinementResult. `refined_boxes` is empty (and
            `triggered=False`) when refinement is disabled, the backend
            is "none", or the overlap analysis did not request
            refinement for this image.
        """
        with timer() as elapsed:
            if not self._config.enabled or self._backend is None or not overlap_result.needs_refinement:
                return RefinementResult(
                    image_id=detection_result.image_id,
                    triggered=False,
                    backend=self._config.backend,
                    processing_time_ms=elapsed["elapsed_ms"],
                )

            indices = self._collect_refinement_indices(overlap_result)
            refined_boxes: list[RefinedBox] = []

            for index in indices:
                if index < 0 or index >= len(detection_result.detections):
                    logger.warning("Ignoring invalid refinement detection_index=%d", index)
                    continue

                detection = detection_result.detections[index]
                try:
                    raw_result = self._backend.refine(image_array, detection.bbox, index)
                except Exception:
                    logger.exception(
                        "Segmentation refinement failed for image_id='%s' "
                        "detection_index=%d; keeping original detection.",
                        detection_result.image_id,
                        index,
                    )
                    continue

                refined_boxes.append(self._apply_output_policy(detection, raw_result))

        result = RefinementResult(
            image_id=detection_result.image_id,
            triggered=True,
            backend=self._config.backend,
            refined_boxes=refined_boxes,
            processing_time_ms=elapsed["elapsed_ms"],
        )

        logger.info(
            "Refiner processed %d detection(s) for image_id='%s' (%.2f ms)",
            len(refined_boxes),
            detection_result.image_id,
            elapsed["elapsed_ms"],
        )
        return result

    def _apply_output_policy(self, detection: Detection, raw_result: RefinedBox) -> RefinedBox:
        """Validates a backend's refinement against configured sanity bounds.

        A backend result is rejected (falls back to the original detector
        bbox) when:
            - `refinement.output.use_mask_bbox` is False, or
            - the backend already reported `used_fallback=True`, or
            - the mask coverage is below `min_mask_coverage_ratio`, or
            - the refined box expanded beyond `max_bbox_expansion_ratio`
              relative to the original detection box.

        Args:
            detection: The original Detection this refinement belongs to.
            raw_result: The raw RefinedBox returned by the backend.

        Returns:
            The validated RefinedBox (either `raw_result` unchanged, or a
            fallback pointing at `detection.bbox`).
        """
        if not self._output_config.use_mask_bbox or raw_result.used_fallback:
            return self._fallback_box(detection, raw_result)

        if raw_result.mask_area_ratio < self._output_config.min_mask_coverage_ratio:
            logger.debug(
                "Rejecting refinement for detection_index=%d: coverage %.3f < min %.3f",
                raw_result.detection_index,
                raw_result.mask_area_ratio,
                self._output_config.min_mask_coverage_ratio,
            )
            return self._fallback_box(detection, raw_result)

        original_area = max(detection.bbox.area, 1.0)
        expansion_ratio = raw_result.refined_bbox.area / original_area
        if expansion_ratio > self._output_config.max_bbox_expansion_ratio:
            logger.debug(
                "Rejecting refinement for detection_index=%d: expansion %.3f > max %.3f",
                raw_result.detection_index,
                expansion_ratio,
                self._output_config.max_bbox_expansion_ratio,
            )
            return self._fallback_box(detection, raw_result)

        return raw_result

    def _fallback_box(self, detection: Detection, raw_result: RefinedBox) -> RefinedBox:
        """Builds a fallback RefinedBox pointing at the original detector bbox.

        Args:
            detection: The original Detection this refinement belongs to.
            raw_result: The raw RefinedBox (used to preserve backend name
                and detection_index).

        Returns:
            A RefinedBox with `used_fallback=True`.
        """
        return RefinedBox(
            detection_index=raw_result.detection_index,
            refined_bbox=detection.bbox,
            mask_area_ratio=raw_result.mask_area_ratio,
            refinement_confidence=raw_result.refinement_confidence,
            backend=raw_result.backend,
            used_fallback=True,
        )

    @staticmethod
    def _collect_refinement_indices(overlap_result: OverlapResult) -> list[int]:
        """Extracts the unique, sorted detection indices requiring refinement.

        Args:
            overlap_result: Result produced by OverlapResolver.

        Returns:
            Sorted unique detection indices across all overlap groups.
        """
        indices: set[int] = set()
        for group in overlap_result.groups:
            indices.update(group.detection_indices)
        return sorted(indices)
