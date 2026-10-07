"""Offline build pipeline for Stocktaking AI.

The build pipeline prepares all persistent data required by runtime
inference:

    data/gallery/
        │
        ├── sync_gallery (catalog.source = sqlite)
        │       -> catalog DB: thư mục mới -> SKU mới (needs_naming), cập nhật số ảnh
        │
        └── GalleryIndexBuilder (retrieval.build_gallery_index; chỉ khi fingerprint đổi)
                -> data/cache/gallery_index.faiss
                -> data/cache/gallery_metadata.json
                -> data/cache/gallery_index.faiss.fingerprint.json

Runtime inference (Retriever) must NEVER rebuild gallery metadata or
gallery embeddings automatically — it only loads what this pipeline
produced.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np

from engine.catalog.factory import open_catalog_repository
from engine.core.config import AppConfig, load_config
from engine.core.logger import get_logger
from engine.core.utils import generate_id
from engine.models.models import ImageData
from engine.retrieval.backends.base import EmbeddingBackend
from engine.retrieval.fingerprint import compute_fingerprint, index_is_current
from engine.retrieval.gallery_builder import GalleryIndexBuilder

logger = get_logger(__name__)


class BuildPipeline:
    """Orchestrates offline product metadata and gallery index building."""

    def __init__(self, config: AppConfig) -> None:
        """Initializes the build pipeline.

        Args:
            config: Fully validated application configuration.
        """
        self._config = config

    def run(self) -> None:
        """Runs the configured offline build process.

        Each stage is independently gated by configuration
        (`catalog.source`, `retrieval.build_gallery_index`).
        """
        logger.info("=" * 72)
        logger.info("Starting Stocktaking AI build pipeline")
        logger.info("=" * 72)

        self._build_metadata()
        self._build_gallery_index()

        logger.info("=" * 72)
        logger.info("Build pipeline completed successfully")
        logger.info("=" * 72)

    def _build_metadata(self) -> None:
        """Đồng bộ thư mục gallery vào catalog DB (nguồn sqlite/database). Nguồn snapshot chỉ đọc: bỏ qua."""
        if self._config.catalog.source == "snapshot":
            logger.info("Catalog source '%s' is read-only; gallery sync skipped.", self._config.catalog.source)
            return
        self._sync_gallery_to_catalog()

    def _sync_gallery_to_catalog(self) -> None:
        """Nguồn sqlite/database: thư mục gallery mới -> SKU mới (needs_naming), cập nhật số ảnh."""
        from engine.catalog.db import make_engine, make_engine_from_url
        from engine.catalog.sync_gallery import sync_gallery

        logger.info("Syncing gallery folders into catalog DB...")
        if self._config.catalog.source == "database":
            engine = make_engine_from_url(self._config.catalog.db_url)
        else:
            engine = make_engine(self._config.resolve_path(self._config.catalog.db_path))
        try:
            result = sync_gallery(engine, self._config.resolve_path(self._config.paths.gallery_dir))
        finally:
            engine.dispose()
        logger.info(
            "Gallery sync: new SKU=%s, image_count updates=%d, missing folders=%d",
            result.created or "-", len(result.plan.image_count_updates), len(result.plan.missing_folders),
        )

    def _build_gallery_index(self) -> None:
        """Builds the persistent gallery FAISS index.

        Controlled by `retrieval.build_gallery_index`. Khi bật, chỉ build lại nếu fingerprint
        (ảnh gallery + ánh xạ thư mục->ID + model + embedding_dim) khác lần build trước.
        """
        if not self._config.retrieval.build_gallery_index:
            logger.info("Gallery index build disabled by configuration.")
            return

        catalog = open_catalog_repository(self._config)
        fingerprint = compute_fingerprint(self._config, catalog.folder_to_product_id())
        if index_is_current(self._config, fingerprint):
            logger.info("Gallery index is up to date (fingerprint %s); skipping rebuild.", fingerprint["digest"][:12])
            return

        logger.info("Building gallery embedding index...")
        try:
            backend = self._create_embedding_backend()
            cropper = self._create_gallery_cropper()
            GalleryIndexBuilder(config=self._config, backend=backend, catalog=catalog, cropper=cropper).build()
            logger.info("Gallery embedding index built successfully.")
        except Exception as exc:
            logger.exception("Gallery index build failed.")
            raise RuntimeError("Gallery index build failed.") from exc

    def _create_gallery_cropper(self) -> Callable[[np.ndarray], np.ndarray | None] | None:
        """Hàm cắt sản phẩm khỏi ảnh gallery theo ``retrieval.gallery_crop`` (None = không cắt)."""
        if self._config.retrieval.gallery_crop == "none":
            return None
        from engine.detection.detector import Detector

        detector = Detector(self._config)
        padding = self._config.cropping.padding_pixels

        def crop(image_array: np.ndarray) -> np.ndarray | None:
            height, width = image_array.shape[:2]
            image = ImageData(
                image_id=generate_id(prefix="gal_"), source_path="", image_array=image_array, width=width, height=height
            )
            detections = detector.detect(image).detections
            if not detections:
                return None
            box = detections[0].bbox  # tin cậy nhất (Detector đã sắp giảm dần)
            x1, y1 = max(0, int(box.x1) - padding), max(0, int(box.y1) - padding)
            x2, y2 = min(width, int(box.x2) + padding), min(height, int(box.y2) + padding)
            if x2 <= x1 or y2 <= y1:
                return None
            return image_array[y1:y2, x1:x2].copy()

        return crop

    def _create_embedding_backend(self) -> EmbeddingBackend:
        """Creates the configured embedding backend.

        Returns:
            An initialized EmbeddingBackend.

        Raises:
            ValueError: If the configured backend is unsupported.
        """
        backend_name = self._config.retrieval.backend

        if backend_name == "siglip2":
            from engine.retrieval.backends.siglip2 import Siglip2Backend

            return Siglip2Backend(self._config.retrieval)

        if backend_name == "mock_visual_embedding":
            from engine.retrieval.backends.mock_visual_embedding import MockVisualEmbeddingBackend

            return MockVisualEmbeddingBackend(self._config.retrieval)

        raise ValueError(
            f"Unsupported retrieval backend: '{backend_name}'. "
            "Available backends: 'siglip2', 'mock_visual_embedding'."
        )


def run_build(config: AppConfig | None = None) -> None:
    """Convenience function for running the build pipeline.

    Args:
        config: Optional application configuration. If omitted, the
            central configuration is loaded.
    """
    if config is None:
        config = load_config()
    BuildPipeline(config).run()


if __name__ == "__main__":
    run_build()
