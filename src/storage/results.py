"""Result persistence module.

Responsibility: export InventoryResult objects to disk as JSON, CSV, and
annotated visualization images. This module must NEVER contain AI logic
or invoke pipeline components directly.
"""

from __future__ import annotations

import csv
import dataclasses
import json
from pathlib import Path

import cv2
import numpy as np

from src.core.config import AppConfig
from src.core.logger import get_logger
from src.core.utils import ensure_dir
from src.models.models import InventoryResult

logger = get_logger(__name__)


class StorageManager:
    """Persists InventoryPipeline outputs to disk in multiple formats."""

    def __init__(self, config: AppConfig) -> None:
        """Initializes the StorageManager with its output configuration.

        Args:
            config: Fully validated application configuration.
        """
        self._config = config.storage
        self._output_dir = config.resolve_path(config.paths.output_dir)
        ensure_dir(self._output_dir)
        logger.info("StorageManager initialized with output_dir='%s'", self._output_dir)

    def save_json(self, result: InventoryResult) -> str:
        """Exports an InventoryResult to a JSON file.

        Args:
            result: The pipeline result to export.

        Returns:
            The string path of the written JSON file.
        """
        output_path = self._output_dir / self._config.json_filename
        payload = dataclasses.asdict(result)

        with open(output_path, "w", encoding="utf-8") as file_handle:
            json.dump(payload, file_handle, indent=2, default=str)

        logger.info("Saved JSON result to '%s'", output_path)
        return str(output_path)

    def save_csv(self, result: InventoryResult) -> str:
        """Exports an InventoryResult's items to a flat CSV file.

        Args:
            result: The pipeline result to export.

        Returns:
            The string path of the written CSV file.
        """
        output_path = self._output_dir / self._config.csv_filename
        fieldnames = [
            "image_id",
            "product_id",
            "product_name",
            "x1",
            "y1",
            "x2",
            "y2",
            "detection_confidence",
            "similarity_score",
            "final_confidence",
            "status",
        ]

        with open(output_path, "w", encoding="utf-8", newline="") as file_handle:
            writer = csv.DictWriter(file_handle, fieldnames=fieldnames)
            writer.writeheader()
            for item in result.items:
                writer.writerow(
                    {
                        "image_id": result.image_id,
                        "product_id": item.product_id,
                        "product_name": item.product_name,
                        "x1": round(item.bbox.x1, 2),
                        "y1": round(item.bbox.y1, 2),
                        "x2": round(item.bbox.x2, 2),
                        "y2": round(item.bbox.y2, 2),
                        "detection_confidence": round(item.detection_confidence, 4),
                        "similarity_score": round(item.similarity_score, 4),
                        "final_confidence": round(item.final_confidence, 4),
                        "status": item.status,
                    }
                )

        logger.info("Saved CSV result to '%s'", output_path)
        return str(output_path)

    def save_annotated_image(self, image: np.ndarray, result: InventoryResult) -> str:
        """Renders bounding boxes/labels onto an image and saves it to disk."""
        annotated = image.copy()
        color = tuple(self._config.box_color)

        img_h, img_w = image.shape[:2]
        diagonal = (img_h ** 2 + img_w ** 2) ** 0.5 

        for item in result.items:
            x1, y1, x2, y2 = (int(item.bbox.x1), int(item.bbox.y1), int(item.bbox.x2), int(item.bbox.y2))
            
            box_w = abs(x2 - x1)
            box_h = abs(y2 - y1)
            box_size = min(box_w, box_h)

            font_scale = max(0.35, min(1.2, (diagonal / 1000.0) * 0.4 + (box_size / img_h) * 0.2))
            thickness = max(1, int(font_scale * 1.8))
            text_thickness = max(1, int(thickness // 1.5))

            cv2.rectangle(annotated, (x1, y1), (x2, y2), color, thickness)
            
            label = f"{item.product_name} {item.final_confidence:.2f}"
            (text_w, text_h), baseline = cv2.getTextSize(
                label, cv2.FONT_HERSHEY_SIMPLEX, font_scale, text_thickness
            )
            
            text_bg_y1 = max(0, y1 - text_h - baseline - 6)
            text_bg_y2 = y1 if y1 - text_h - baseline - 6 >= 0 else y1 + text_h + baseline + 6
            text_y = max(text_h, y1 - baseline - 2) if y1 - text_h - baseline - 6 >= 0 else y1 + text_h + 2

            cv2.rectangle(
                annotated, 
                (x1, text_bg_y1), 
                (min(img_w, x1 + text_w + 6), text_bg_y2), 
                color, 
                -1
            )
            
            cv2.putText(
                annotated,
                label,
                (x1 + 3, text_y),
                cv2.FONT_HERSHEY_SIMPLEX,
                font_scale,
                (255, 255, 255),
                text_thickness,
                cv2.LINE_AA,
            )

        output_path = self._output_dir / self._config.annotated_image_filename
        success = cv2.imwrite(str(output_path), annotated)
        if not success:
            raise ValueError(f"Failed to write annotated image to '{output_path}'")

        logger.info("Saved annotated image to '%s'", output_path)
        return str(output_path)

    def save_all(self, image: np.ndarray, result: InventoryResult) -> dict[str, str]:
        """Convenience method exporting JSON, CSV, and annotated image at once.

        Each individual export is gated by its own configuration toggle
        (`storage.save_json`, `storage.save_csv`, `storage.save_annotated_image`).

        Args:
            image: The original source image (BGR) that was processed.
            result: The pipeline result to export.

        Returns:
            A dictionary mapping artifact name to the saved file path, for
            only the artifacts that were actually enabled and written.
        """
        saved: dict[str, str] = {}
        if self._config.save_json:
            saved["json"] = self.save_json(result)
        if self._config.save_csv:
            saved["csv"] = self.save_csv(result)
        if self._config.save_annotated_image:
            saved["annotated_image"] = self.save_annotated_image(image, result)
        return saved