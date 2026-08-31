"""Gallery index builder.

Builds the persistent FAISS gallery index used by the retrieval system.
This module is used by ``BuildPipeline`` only — runtime inference uses
``Retriever`` to *load* a pre-built index, never to build one.

Responsibilities:
    - Scan product images from the gallery directory.
    - Resolve each gallery folder to its stable internal product_id via
      ``data/metadata/product_ids.json`` (produced by MetadataBuilder).
    - Generate embeddings using the configured EmbeddingBackend.
    - Build a FAISS inner-product index.
    - Save the FAISS index and vector-to-product metadata to disk.

Gallery structure:

    data/gallery/
    ├── 1000000008/
    │   ├── 01.jpg
    │   └── ...
    └── マジョリカマジョルカ　シャドーカスタマイズ（BE203）/
        └── ...

Generated files:

    data/cache/gallery_index.faiss
    data/cache/gallery_metadata.json

gallery_metadata.json stores only the stable internal product_id (string)
required to resolve a FAISS vector back to a product:

    [
        {"product_id": "1"},
        {"product_id": "1"},
        {"product_id": "2"}
    ]

Important:
    - FAISS vector order and gallery_metadata.json order are identical.
    - Every gallery folder must exist in product_ids.json.
    - The build fails before embedding if the mappings are inconsistent.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import faiss
import numpy as np

from src.core.config import AppConfig
from src.core.logger import get_logger
from src.core.utils import ensure_dir, list_image_files, load_image_bgr
from src.retrieval.backends.base import EmbeddingBackend

logger = get_logger(__name__)


class GalleryIndexBuilder:
    """Builds a FAISS index from the product gallery."""

    def __init__(self, config: AppConfig, backend: EmbeddingBackend) -> None:
        """Initializes the gallery index builder.

        Args:
            config: Fully validated application configuration.
            backend: Initialized embedding backend used to generate
                gallery embeddings.
        """
        self._config = config.retrieval
        self._backend = backend
        self._gallery_dir = config.resolve_path(config.paths.gallery_dir)
        self._index_path = config.resolve_path(self._config.gallery_index_path)
        self._metadata_path = config.resolve_path(self._config.gallery_metadata_path)
        self._product_ids_path = (
            config.resolve_path(config.paths.metadata_dir) / config.catalog.product_ids_filename
        )
        self._embedding_dim = self._config.embedding_dim

    # =========================================================================
    # Public API
    # =========================================================================

    def build(self) -> tuple[faiss.Index, list[dict]]:
        """Builds and saves the gallery FAISS index.

        Returns:
            Tuple of (FAISS index, vector-to-product metadata).

        Raises:
            FileNotFoundError: If the gallery or product_ids.json is missing.
            ValueError: If gallery folders and product IDs are inconsistent.
            RuntimeError: If no embeddings could be generated, or vector
                counts do not match expectations per product.
        """
        logger.info("Starting gallery index build from '%s'.", self._gallery_dir)

        self._validate_gallery()
        folder_to_product_id = self._load_product_id_mapping()

        product_dirs = sorted(
            (path for path in self._gallery_dir.iterdir() if path.is_dir()),
            key=lambda path: path.name,
        )
        logger.info("Found %d product folder(s) in gallery.", len(product_dirs))

        self._validate_product_mapping(product_dirs, folder_to_product_id)

        vectors: list[np.ndarray] = []
        metadata: list[dict] = []
        vector_counts_by_product: Counter[str] = Counter()

        total_images = sum(len(list_image_files(product_dir)) for product_dir in product_dirs)
        processed = 0
        skipped = 0

        for product_dir in product_dirs:
            product_id = folder_to_product_id[product_dir.name]
            image_paths = list_image_files(product_dir)
            logger.info("Product ID %s | folder='%s' | images=%d", product_id, product_dir.name, len(image_paths))

            for image_path in image_paths:
                processed += 1
                if processed == 1 or processed % 20 == 0 or processed == total_images:
                    progress = processed * 100.0 / total_images if total_images else 100.0
                    logger.info("Embedding image %d/%d (%.1f%%): %s", processed, total_images, progress, image_path.name)

                try:
                    image_array = load_image_bgr(image_path)
                    embedding = self._embed(image_array)
                except (ValueError, RuntimeError):
                    skipped += 1
                    logger.exception("Failed to process gallery image '%s'; skipping.", image_path)
                    continue

                vectors.append(embedding)
                metadata.append({"product_id": product_id})
                vector_counts_by_product[product_id] += 1

        if skipped:
            logger.warning("Skipped %d gallery image(s) due to processing errors.", skipped)

        index = self._create_index(vectors)
        self._validate_index_metadata_consistency(index, metadata)
        self._validate_vector_counts(product_dirs, folder_to_product_id, vector_counts_by_product)

        self._save_index(index)
        self._save_metadata(metadata)

        logger.info("Gallery index built successfully.")
        logger.info("FAISS vectors: %d | Metadata entries: %d", index.ntotal, len(metadata))
        logger.info(
            "Products: %d | Images processed: %d | Images skipped: %d",
            len(product_dirs), processed, skipped,
        )

        return index, metadata

    # =========================================================================
    # Product ID mapping
    # =========================================================================

    def _load_product_id_mapping(self) -> dict[str, str]:
        """Loads the stable product ID mapping (folder name -> product_id).

        Returns:
            Mapping from gallery folder name to internal product_id (string).

        Raises:
            FileNotFoundError: If product_ids.json does not exist.
            ValueError: If the mapping is invalid.
        """
        if not self._product_ids_path.is_file():
            raise FileNotFoundError(
                f"Product ID mapping does not exist: {self._product_ids_path}. "
                "Run the metadata build step before building the gallery index."
            )

        try:
            with self._product_ids_path.open("r", encoding="utf-8") as file_handle:
                data = json.load(file_handle)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON product ID mapping: {self._product_ids_path}") from exc

        products = data.get("products") if isinstance(data, dict) else None
        if not isinstance(products, dict):
            raise ValueError("product_ids.json must contain a 'products' object.")

        folder_to_product_id: dict[str, str] = {}
        for internal_id, folder in products.items():
            try:
                numeric_id = int(internal_id)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Invalid internal product ID in product_ids.json: {internal_id!r}") from exc
            if numeric_id <= 0 or not isinstance(folder, str) or not folder:
                raise ValueError(f"Invalid product_ids.json entry: {internal_id!r} -> {folder!r}")

            if folder in folder_to_product_id:
                raise ValueError(
                    f"Duplicate gallery folder mapping in product_ids.json: '{folder}' "
                    f"is mapped to both ID {folder_to_product_id[folder]} and ID {internal_id}."
                )
            folder_to_product_id[folder] = str(numeric_id)

        logger.info("Loaded %d stable product ID mapping(s).", len(folder_to_product_id))
        return folder_to_product_id

    @staticmethod
    def _validate_product_mapping(product_dirs: list[Path], folder_to_product_id: dict[str, str]) -> None:
        """Validates gallery folders against product_ids.json (both directions)."""
        gallery_folders = {path.name for path in product_dirs}
        mapped_folders = set(folder_to_product_id.keys())

        missing_from_mapping = gallery_folders - mapped_folders
        if missing_from_mapping:
            for folder in sorted(missing_from_mapping):
                logger.error("Missing product ID mapping: '%s'", folder)
            raise ValueError(
                "Gallery contains folder(s) not registered in product_ids.json. Build aborted."
            )

        missing_from_gallery = mapped_folders - gallery_folders
        if missing_from_gallery:
            for folder in sorted(missing_from_gallery):
                logger.warning("Product ID %s -> gallery folder not found: '%s'", folder_to_product_id[folder], folder)

    @staticmethod
    def _validate_vector_counts(
        product_dirs: list[Path],
        folder_to_product_id: dict[str, str],
        vector_counts_by_product: Counter[str],
    ) -> None:
        """Validates that each product received the expected number of vectors."""
        errors: list[str] = []
        for product_dir in product_dirs:
            product_id = folder_to_product_id[product_dir.name]
            expected = len(list_image_files(product_dir))
            actual = vector_counts_by_product.get(product_id, 0)
            if expected != actual:
                errors.append(f"ID {product_id} ('{product_dir.name}'): expected {expected}, got {actual}")

        if errors:
            for error in errors:
                logger.error("  %s", error)
            raise RuntimeError("Gallery embedding validation failed: vector count mismatch.")

    # =========================================================================
    # Embedding / FAISS
    # =========================================================================

    def _embed(self, image_array: np.ndarray) -> np.ndarray:
        """Generates a normalized, fixed-dimension embedding for one image."""
        embedding = np.asarray(self._backend.embed(image_array), dtype=np.float32).reshape(-1)

        if embedding.shape[0] < self._embedding_dim:
            embedding = np.pad(embedding, (0, self._embedding_dim - embedding.shape[0]))
        elif embedding.shape[0] > self._embedding_dim:
            embedding = embedding[: self._embedding_dim]

        norm = np.linalg.norm(embedding)
        if norm <= 1e-8:
            raise ValueError("Generated embedding has zero norm.")
        return (embedding / norm).astype(np.float32)

    def _create_index(self, vectors: list[np.ndarray]) -> faiss.Index:
        """Creates a FAISS inner-product index from a list of vectors."""
        if not vectors:
            raise RuntimeError("No gallery embeddings were generated. Cannot create a valid gallery index.")

        index = faiss.IndexFlatIP(self._embedding_dim)
        matrix = np.vstack(vectors).astype(np.float32)
        index.add(matrix)
        return index

    def _save_index(self, index: faiss.Index) -> None:
        """Saves the FAISS index to disk."""
        ensure_dir(self._index_path.parent)
        faiss.write_index(index, str(self._index_path))
        logger.info("Saved gallery FAISS index to '%s'.", self._index_path)

    def _save_metadata(self, metadata: list[dict]) -> None:
        """Saves the vector-to-product mapping to disk."""
        ensure_dir(self._metadata_path.parent)
        with self._metadata_path.open("w", encoding="utf-8") as file_handle:
            json.dump(metadata, file_handle, ensure_ascii=False, indent=2)
            file_handle.write("\n")
        logger.info("Saved gallery metadata to '%s'.", self._metadata_path)

    @staticmethod
    def _validate_index_metadata_consistency(index: faiss.Index, metadata: list[dict]) -> None:
        """Ensures FAISS vector count matches metadata entry count."""
        if index.ntotal != len(metadata):
            raise RuntimeError(
                f"FAISS/metadata mismatch: FAISS vectors={index.ntotal}, metadata entries={len(metadata)}."
            )

    def _validate_gallery(self) -> None:
        """Validates the gallery directory exists."""
        if not self._gallery_dir.exists():
            raise FileNotFoundError(f"Gallery directory does not exist: {self._gallery_dir}")
        if not self._gallery_dir.is_dir():
            raise NotADirectoryError(f"Gallery path is not a directory: {self._gallery_dir}")


def build_gallery_index(config: AppConfig, backend: EmbeddingBackend) -> tuple[faiss.Index, list[dict]]:
    """Convenience function: builds the FAISS gallery index.

    Args:
        config: Fully validated application configuration.
        backend: Embedding backend used for gallery images.

    Returns:
        Tuple of (FAISS index, vector-to-product metadata).
    """
    return GalleryIndexBuilder(config=config, backend=backend).build()
