"""Barcode decoding plugin.

Decodes 1D/2D barcodes present within a product crop via `pyzbar`. Runs
on `crop.raw_image_array` (original resolution) rather than
`crop.image_array` — barcodes become unreadable once downsized to the
retrieval crop size.
"""

from __future__ import annotations

import cv2
from pyzbar import pyzbar

from src.core.config import AppConfig
from src.core.logger import get_logger
from src.core.utils import timer
from src.models.models import CropImage

logger = get_logger(__name__)


class BarcodePlugin:
    """Decodes 1D/2D barcodes from a product crop."""

    name = "barcode"

    def __init__(self, config: AppConfig) -> None:
        """Initializes the barcode plugin with its configuration.

        Args:
            config: Fully validated application configuration.
        """
        self._config = config.plugins.barcode
        logger.info("BarcodePlugin initialized (enabled=%s)", self._config.enabled)

    def is_enabled(self) -> bool:
        """Returns whether this plugin is enabled via configuration."""
        return self._config.enabled

    def run(self, crop: CropImage) -> dict:
        """Decodes any barcodes present within the crop's original-resolution image.

        Args:
            crop: The cropped product image to analyze.

        Returns:
            A dictionary with keys:
                barcodes: List of {"data": str, "type": str} decoded values.
                confidence_boost: Confidence contribution if at least one
                    barcode was successfully decoded, otherwise 0.0.
        """
        with timer() as elapsed:
            gray = cv2.cvtColor(crop.raw_image_array, cv2.COLOR_BGR2GRAY)
            try:
                decoded_objects = pyzbar.decode(gray)
            except Exception:  # noqa: BLE001 - zbar backend errors vary by platform
                logger.exception("Barcode decoding failed for crop_id='%s'", crop.crop_id)
                decoded_objects = []

            barcodes = [
                {"data": obj.data.decode("utf-8", errors="ignore"), "type": obj.type}
                for obj in decoded_objects
            ]
            boost = self._config.confidence_boost if barcodes else 0.0

        logger.info(
            "BarcodePlugin decoded %d barcode(s) for crop_id='%s' (%.2f ms)",
            len(barcodes),
            crop.crop_id,
            elapsed["elapsed_ms"],
        )

        return {
            "barcodes": barcodes,
            "confidence_boost": boost,
        }