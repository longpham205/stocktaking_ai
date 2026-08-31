"""Abstract interface shared by every segmentation/refinement backend.

Adding a new refinement model (e.g. a different segmentation network)
means: (1) create a new file in this package that implements
`SegmentationBackend`, and (2) register its name in
`Refiner._load_backend` (src/segmentation/refiner.py). No other module
needs to change — the pipeline never imports SAM2 (or any concrete
backend) directly, only `Refiner` (03_DEVELOPMENT_RULES.md, Rule 25 -
Extensibility Standard).
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np

from src.models.models import BoundingBox, RefinedBox


class SegmentationBackend(ABC):
    """Base interface every segmentation/refinement backend must implement."""

    @abstractmethod
    def refine(
        self,
        image_array: np.ndarray,
        bbox: BoundingBox,
        detection_index: int,
    ) -> RefinedBox:
        """Refines a single detection's bounding box using segmentation.

        Implementations should load their weights once in `__init__` (see
        03_DEVELOPMENT_RULES.md, Rule 23) and must NOT reload them here.
        Implementations must never raise for a "bad" mask — instead they
        should return a `RefinedBox` with `used_fallback=True` and
        `refined_bbox` equal to the original `bbox`; `Refiner` treats
        actual exceptions as hard failures.

        Args:
            image_array: BGR pixel array of the full source image (the
                backend is responsible for cropping/prompting internally).
            bbox: The original detector bounding box to refine.
            detection_index: Index into the originating
                `DetectionResult.detections` list, carried through
                unchanged so `Cropper` can map refinements back.

        Returns:
            A `RefinedBox` describing the refinement outcome.
        """
        raise NotImplementedError
