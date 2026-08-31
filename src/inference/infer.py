"""Inference mode execution runner.

Responsibility: load images from disk, invoke `InventoryPipeline.run()`,
and forward results to the StorageManager for persistence (see
02_MODULE_SPECIFICATION.md, Section 8).
"""

from __future__ import annotations

from pathlib import Path

from src.core.config import AppConfig
from src.core.logger import get_logger
from src.core.utils import generate_id, list_image_files, load_image_bgr
from src.models.models import ImageData, InventoryResult
from src.pipeline.pipeline import InventoryPipeline
from src.storage.results import StorageManager

logger = get_logger(__name__)


class InferenceRunner:
    """Executes Inference Mode: query image(s) -> InventoryResult -> Storage."""

    def __init__(self, config: AppConfig) -> None:
        """Initializes the InferenceRunner, its pipeline, and storage once.

        Args:
            config: Fully validated application configuration.
        """
        self._config = config
        self._pipeline = InventoryPipeline(config)
        self._storage = StorageManager(config)
        logger.info("InferenceRunner initialized.")

    def run_single(self, image_path: str, similarity_threshold: float | None = None) -> InventoryResult:
        """Runs the full pipeline on a single query image and persists results.

        Args:
            image_path: Filesystem path to the query image.
            similarity_threshold: Optional per-call override of
                `decision.similarity_threshold` (see
                `InventoryPipeline.run`).

        Returns:
            The InventoryResult produced by the pipeline.

        Raises:
            FileNotFoundError: If the image path does not exist.
            ValueError: If the image could not be decoded.
        """
        path = Path(image_path)
        if not path.is_file():
            raise FileNotFoundError(f"Query image not found: '{image_path}'")

        image_array = load_image_bgr(path)
        height, width = image_array.shape[:2]

        image_data = ImageData(
            image_id=generate_id(prefix="img_"),
            source_path=str(path),
            image_array=image_array,
            width=width,
            height=height,
        )

        logger.info("Running inference on '%s'", path)
        result = self._pipeline.run(image_data, similarity_threshold=similarity_threshold)
        self._storage.save_all(image_array, result)
        return result

    def run_batch(self, image_dir: str, similarity_threshold: float | None = None) -> list[InventoryResult]:
        """Runs the full pipeline over every image in a directory.

        Args:
            image_dir: Directory containing query images.
            similarity_threshold: Optional per-call override of
                `decision.similarity_threshold` (see
                `InventoryPipeline.run`).

        Returns:
            List of InventoryResult objects, one per successfully processed
            image. Each result also triggers a full storage export using
            the shared output filenames configured in `configs/config.yaml`
            (later images overwrite earlier exports); callers needing
            per-image artifacts should call `run_single` in a loop with a
            custom output directory instead.
        """
        image_paths = list_image_files(image_dir)
        if not image_paths:
            logger.warning("No supported image files found in '%s'", image_dir)
            return []

        results: list[InventoryResult] = []
        for image_path in image_paths:
            try:
                results.append(self.run_single(str(image_path), similarity_threshold=similarity_threshold))
            except (FileNotFoundError, ValueError):
                logger.exception("Failed to process '%s'; skipping.", image_path)
                continue

        logger.info("Batch inference completed: %d/%d image(s) processed.", len(results), len(image_paths))
        return results
