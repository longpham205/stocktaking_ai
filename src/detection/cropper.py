"""Product region cropping module.

Responsibility: slice detected bounding box regions out of the source
image array, with boundary clipping. This module must NEVER run
detection or retrieval algorithms, and must NEVER mutate the original
DetectionResult's bounding box values (see 02_MODULE_SPECIFICATION.md,
Section 3.2).

Each crop is produced in two resolutions (see `CropImage` docstring):
`image_array` (resized to `cropping.target_size`, for Retriever) and
`raw_image_array` (original resolution, boundary-clipped only, for
OCR/Color/Barcode plugins — resizing destroys small packaging text and
barcode detail before those plugins ever see it).
"""

from __future__ import annotations

import cv2
import numpy as np

from src.core.config import AppConfig
from src.core.logger import get_logger
from src.core.utils import clip_coordinate, generate_id, timer
from src.models.models import (
    BoundingBox,
    CropImage,
    DetectionResult,
    ImageData,
    RefinementResult,
)

logger = get_logger(__name__)


class Cropper:
    """Extracts sub-image crops for every detection in a DetectionResult."""

    def __init__(self, config: AppConfig) -> None:
        """Initializes the Cropper with its configuration.

        Args:
            config: Fully validated application configuration.
        """
        self._padding = config.cropping.padding_pixels
        target = config.cropping.target_size
        self._target_size = (int(target[0]), int(target[1]))
        self._use_refined_bbox = config.cropping.use_refined_bbox
        logger.info(
            "Cropper initialized with padding=%dpx target_size=%s use_refined_bbox=%s",
            self._padding,
            self._target_size,
            self._use_refined_bbox,
        )

    def crop(
        self,
        image_data: ImageData,
        detection_result: DetectionResult,
        refinement_result: RefinementResult,
    ) -> list[CropImage]:
        """Crops every detected region out of the source image.

        Args:
            image_data: The original source image.
            detection_result: Detections produced by the Detector module.
                Never mutated.
            refinement_result: Segmentation refinement output produced by
                Refiner. May have empty `refined_boxes` (refinement not
                triggered/disabled) — in that case every crop uses the
                original detector bbox.

        Returns:
            List of CropImage objects, one per valid detection. Detections
            that clip down to a zero-area region are silently skipped.
        """
        with timer() as elapsed:
            crops: list[CropImage] = []
            height, width = image_data.image_array.shape[:2]

            for detection_index, detection in enumerate(detection_result.detections):
                bbox, used_refined = self._select_bbox(detection_index, detection.bbox, refinement_result)

                x1 = int(clip_coordinate(bbox.x1 - self._padding, 0, width))
                y1 = int(clip_coordinate(bbox.y1 - self._padding, 0, height))
                x2 = int(clip_coordinate(bbox.x2 + self._padding, 0, width))
                y2 = int(clip_coordinate(bbox.y2 + self._padding, 0, height))

                if x2 <= x1 or y2 <= y1:
                    logger.warning(
                        "Skipping zero-area crop for image_id='%s' detection_index=%d bbox=%s",
                        image_data.image_id,
                        detection_index,
                        bbox.as_list(),
                    )
                    continue

                # Original-resolution crop (boundary-clipped + padded only,
                # never resized) -- used by OCR/Color/Barcode plugins.
                raw_region = image_data.image_array[y1:y2, x1:x2].copy()

                # Normalized-resolution crop -- used by Retriever's
                # embedding backend.
                normalized = cv2.resize(raw_region, self._target_size, interpolation=cv2.INTER_AREA)

                crops.append(
                    CropImage(
                        crop_id=generate_id(prefix="crop_"),
                        image_id=image_data.image_id,
                        image_array=normalized,
                        raw_image_array=raw_region,
                        source_bbox=bbox,
                        detection_confidence=detection.confidence,
                        detection_index=detection_index,
                        used_refined_bbox=used_refined,
                    )
                )

        logger.info(
            "Cropper produced %d crop(s) for image_id='%s' (%.2f ms)",
            len(crops),
            image_data.image_id,
            elapsed["elapsed_ms"],
        )
        return crops

    def _select_bbox(
        self,
        detection_index: int,
        original_bbox: BoundingBox,
        refinement_result: RefinementResult,
    ) -> tuple[BoundingBox, bool]:
        """Chooses the refined bbox or the original detector bbox for a crop.

        Args:
            detection_index: Index of the detection within DetectionResult.
            original_bbox: The detector's original bounding box.
            refinement_result: Segmentation refinement output.

        Returns:
            Tuple of (bbox to crop from, whether it came from refinement).
        """
        if not self._use_refined_bbox:
            return original_bbox, False

        refined_box = refinement_result.get_refined_box(detection_index)
        if refined_box is None or refined_box.used_fallback:
            return original_bbox, False

        return refined_box.refined_bbox, True

    @staticmethod
    def _to_uint8(array: np.ndarray) -> np.ndarray:
        """Ensures an image array is in uint8 format.

        Args:
            array: Input image array of any numeric dtype.

        Returns:
            The array cast to uint8, clipped to the valid [0, 255] range.
        """
        if array.dtype == np.uint8:
            return array
        return np.clip(array, 0, 255).astype(np.uint8)