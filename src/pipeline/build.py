"""Offline build pipeline for Stocktaking AI.

The build pipeline prepares all persistent data required by runtime
inference:

    data/gallery/
        │
        ├── MetadataBuilder
        │       -> data/metadata/products.json
        │       -> data/metadata/product_ids.json
        │
        └── GalleryIndexBuilder
                -> data/cache/gallery_index.faiss
                -> data/cache/gallery_metadata.json

Each stage is independently controlled by configuration:

    catalog.build_metadata
    retrieval.build_gallery_index

Runtime inference (Retriever) must NEVER rebuild gallery metadata or
gallery embeddings automatically — it only loads what this pipeline
produced.
"""

from __future__ import annotations

from src.catalog.metadata import MetadataBuilder
from src.core.config import AppConfig, load_config
from src.core.logger import get_logger
from src.retrieval.backends.base import EmbeddingBackend
from src.retrieval.gallery_builder import GalleryIndexBuilder

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
        (`catalog.build_metadata`, `retrieval.build_gallery_index`).
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
        """Builds the product catalog from gallery directories.

        Controlled by `catalog.build_metadata`.
        """
        if not self._config.catalog.build_metadata:
            logger.info("Product metadata build disabled by configuration.")
            return

        logger.info("Building product metadata...")
        try:
            MetadataBuilder(self._config).build()
            logger.info("Product metadata built successfully.")
        except Exception as exc:
            logger.exception("Product metadata build failed.")
            raise RuntimeError("Product metadata build failed.") from exc

    def _build_gallery_index(self) -> None:
        """Builds the persistent gallery FAISS index.

        Controlled by `retrieval.build_gallery_index`.
        """
        if not self._config.retrieval.build_gallery_index:
            logger.info("Gallery index build disabled by configuration.")
            return

        logger.info("Building gallery embedding index...")
        try:
            backend = self._create_embedding_backend()
            GalleryIndexBuilder(config=self._config, backend=backend).build()
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
            from src.retrieval.backends.siglip2 import Siglip2Backend

            return Siglip2Backend(self._config.retrieval)

        if backend_name == "mock_visual_embedding":
            from src.retrieval.backends.mock_visual_embedding import MockVisualEmbeddingBackend

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
