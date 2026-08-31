"""Classical computer-vision embedding backend: color histogram + texture.

Requires no downloaded neural network weights, matching the v0.1.0 scope
of "Mock/Single-model Retriever" (01_PROJECT_CONTEXT.md, Section 10). This
is the default `retrieval.backend` in `configs/config.yaml`.
"""

from __future__ import annotations

import cv2
import numpy as np

from src.core.config import RetrievalSection
from src.core.logger import get_logger
from src.retrieval.backends.base import EmbeddingBackend

logger = get_logger(__name__)


class MockVisualEmbeddingBackend(EmbeddingBackend):
    """HSV color histogram + downsampled texture embedding (no neural weights)."""

    def __init__(self, config: RetrievalSection) -> None:
        """Initializes the backend with its histogram parameters.

        Args:
            config: The `retrieval` section of the application configuration.
        """
        self._bins = config.color_hist_bins
        logger.info("MockVisualEmbeddingBackend initialized (color_hist_bins=%d)", self._bins)

    def embed(self, image_array: np.ndarray) -> np.ndarray:
        """Computes an HSV color histogram + grayscale texture embedding.

        Args:
            image_array: BGR pixel array (crop or gallery image).

        Returns:
            A 1-D float32 embedding vector concatenating the color
            histogram and texture signature (not yet normalized/resized).
        """
        hsv = cv2.cvtColor(image_array, cv2.COLOR_BGR2HSV)
        hist = cv2.calcHist(
            [hsv],
            [0, 1, 2],
            None,
            [self._bins, self._bins, self._bins],
            [0, 180, 0, 256, 0, 256],
        )
        color_signature = hist.flatten()

        gray = cv2.cvtColor(image_array, cv2.COLOR_BGR2GRAY)
        texture_side = 16
        texture = cv2.resize(gray, (texture_side, texture_side), interpolation=cv2.INTER_AREA)
        texture_signature = texture.flatten().astype(np.float32)

        return np.concatenate([color_signature, texture_signature]).astype(np.float32)
