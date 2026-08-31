"""Classical computer-vision detection backend: Canny edges + contours.

Requires no downloaded neural network weights, matching the v0.1.0 scope
of "Mock/Single-model Detector" (01_PROJECT_CONTEXT.md, Section 10). This
is the default `detection.backend` in `configs/config.yaml`.
"""

from __future__ import annotations

import cv2
import numpy as np

from src.core.config import DetectionSection
from src.core.logger import get_logger
from src.core.utils import clip_coordinate
from src.detection.backends.base import DetectionBackend
from src.models.models import BoundingBox, Detection

logger = get_logger(__name__)


class MockContourBackend(DetectionBackend):
    """Canny edge + contour extraction detector (no neural weights)."""

    def __init__(self, config: DetectionSection) -> None:
        """Initializes the backend with its classical CV parameters.

        Args:
            config: The `detection` section of the application configuration.
        """
        self._config = config
        logger.info(
            "MockContourBackend initialized (canny_t1=%d canny_t2=%d blur_kernel=%d)",
            config.canny_threshold_1,
            config.canny_threshold_2,
            config.blur_kernel_size,
        )

    def detect(self, image_array: np.ndarray) -> list[Detection]:
        """Runs Canny edge detection + contour extraction on the image.

        Args:
            image_array: BGR pixel array of the full source image.

        Returns:
            List of Detection objects passing the configured area,
            aspect-ratio, and confidence filters.
        """
        height, width = image_array.shape[:2]
        image_area = float(height * width)

        gray = cv2.cvtColor(image_array, cv2.COLOR_BGR2GRAY)
        kernel = max(1, self._config.blur_kernel_size | 1)  # force odd kernel
        blurred = cv2.GaussianBlur(gray, (kernel, kernel), 0)
        edges = cv2.Canny(blurred, self._config.canny_threshold_1, self._config.canny_threshold_2)
        dilated = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=2)

        contours, _ = cv2.findContours(dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        detections: list[Detection] = []
        for contour in contours:
            x, y, w, h = cv2.boundingRect(contour)
            box_area = float(w * h)
            if box_area <= 0:
                continue

            area_ratio = box_area / image_area
            aspect_ratio = w / float(h) if h > 0 else 0.0

            if not (self._config.min_box_area_ratio <= area_ratio <= self._config.max_box_area_ratio):
                continue
            if not (self._config.min_aspect_ratio <= aspect_ratio <= self._config.max_aspect_ratio):
                continue

            contour_area = cv2.contourArea(contour)
            rectangularity = clip_coordinate(contour_area / box_area, 0.0, 1.0) if box_area > 0 else 0.0
            confidence = clip_coordinate(0.5 * rectangularity + 0.5 * min(area_ratio / 0.05, 1.0), 0.0, 1.0)

            if confidence < self._config.confidence_threshold:
                continue

            bbox = BoundingBox(
                x1=clip_coordinate(x, 0, width),
                y1=clip_coordinate(y, 0, height),
                x2=clip_coordinate(x + w, 0, width),
                y2=clip_coordinate(y + h, 0, height),
            )
            detections.append(
                Detection(
                    bbox=bbox,
                    confidence=confidence,
                    class_id=0,
                    class_name="product",
                )
            )

        return detections
