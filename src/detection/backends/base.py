"""Abstract interface shared by every detection backend.

Adding a new detection model (e.g. YOLO, a fine-tuned RF-DETR variant, a
different DETR flavor) means: (1) create a new file in this package that
implements `DetectionBackend`, and (2) register its name in
`Detector._load_backend` (src/detection/detector.py). No other file needs
to change — `Detector.detect(image_data) -> DetectionResult` and every
downstream module (Cropper, Pipeline, ...) stay exactly the same
(03_DEVELOPMENT_RULES.md, Rule 25 - Extensibility Standard).
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np

from src.models.models import Detection


class DetectionBackend(ABC):
    """Base interface every detection model backend must implement."""

    @abstractmethod
    def detect(self, image_array: np.ndarray) -> list[Detection]:
        """Runs detection on a full BGR source image.

        Implementations should load their weights once in `__init__` (see
        03_DEVELOPMENT_RULES.md, Rule 23) and must NOT reload them here.

        Args:
            image_array: BGR pixel array of the full source image.

        Returns:
            Raw list of Detection objects (bbox + confidence + class
            info). Sorting by confidence and capping at `max_detections`
            is the caller's (`Detector`) responsibility, not the
            backend's.
        """
        raise NotImplementedError
