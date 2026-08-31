"""Mock refinement backend: returns the original bbox unchanged.

Used when `refinement.backend: "none"`, or as a safe fallback in tests
and environments without SAM2 installed. Requires no downloaded weights.
"""

from __future__ import annotations

from src.core.logger import get_logger
from src.models.models import BoundingBox, RefinedBox

logger = get_logger(__name__)


class MockRefinerBackend:
    """Passthrough refinement backend; always returns the original bbox."""

    def __init__(self) -> None:
        """Initializes the mock refiner (no state, no weights)."""
        logger.info("MockRefinerBackend initialized (no-op passthrough).")

    def refine(
        self,
        image_array,  # noqa: ANN001 - unused, kept for interface parity
        bbox: BoundingBox,
        detection_index: int,
    ) -> RefinedBox:
        """Returns a RefinedBox equal to the original bbox (fallback).

        Args:
            image_array: Unused; present only to satisfy the
                `SegmentationBackend` interface.
            bbox: The original detector bounding box.
            detection_index: Index into the originating DetectionResult.

        Returns:
            A RefinedBox with `used_fallback=True` and `refined_bbox`
            identical to `bbox`.
        """
        return RefinedBox(
            detection_index=detection_index,
            refined_bbox=bbox,
            mask_area_ratio=1.0,
            refinement_confidence=0.0,
            backend="mock_refiner",
            used_fallback=True,
        )
