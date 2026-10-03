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

from engine.catalog.factory import open_catalog_repository
from engine.core.config import AppConfig, load_config
from engine.core.logger import get_logger
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
        """Đồng bộ thư mục gallery vào catalog DB (nguồn sqlite). Nguồn snapshot chỉ đọc: bỏ qua."""
        if self._config.catalog.source != "sqlite":
            logger.info("Catalog source '%s' is read-only; gallery sync skipped.", self._config.catalog.source)
            return
        self._sync_gallery_to_catalog()

    def _sync_gallery_to_catalog(self) -> None:
        """Nguồn sqlite: thư mục gallery mới -> SKU mới (needs_naming), cập nhật số ảnh."""
        from engine.catalog.db import make_engine
        from engine.catalog.sync_gallery import sync_gallery

        logger.info("Syncing gallery folders into catalog DB...")
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
            GalleryIndexBuilder(config=self._config, backend=backend, catalog=catalog).build()
            logger.info("Gallery embedding index built successfully.")
        except Exception as exc:
            logger.exception("Gallery index build failed.")
            raise RuntimeError("Gallery index build failed.") from exc

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
