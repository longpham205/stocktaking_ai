"""Gallery index builder.

Builds the persistent FAISS gallery index used by the retrieval system.
This module is used by ``BuildPipeline`` only — runtime inference uses
``Retriever`` to *load* a pre-built index, never to build one.

Responsibilities:
    - Scan product images from the gallery directory.
    - Resolve each gallery folder to its stable internal product_id via
      ``CatalogRepository.folder_to_product_id()`` (engine/catalog).
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
    - Every gallery folder must be mapped in the catalog.
    - A fingerprint file is written next to the index (engine/retrieval/fingerprint.py).
    - The build fails before embedding if the mappings are inconsistent.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import cv2
import faiss
import numpy as np

from engine.catalog.factory import open_catalog_repository
from engine.catalog.repository import BaseCatalogRepository
from engine.core.config import AppConfig, GalleryAugmentSection
from engine.retrieval.fingerprint import compute_fingerprint, write_fingerprint
from engine.core.logger import get_logger
from engine.core.utils import ensure_dir, list_image_files, load_image_bgr
from engine.retrieval.backends.base import EmbeddingBackend

logger = get_logger(__name__)

_ROTATE_CODES = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}


def augment_views(image_array: np.ndarray, augment: GalleryAugmentSection) -> list[np.ndarray]:
    """Các bản biến đổi của một ảnh gallery (không gồm ảnh gốc), theo thứ tự cố định.

    Args:
        image_array: Ảnh gallery BGR.
        augment: Cấu hình ``retrieval.augment``.

    Returns:
        Mỗi góc xoay một ảnh, rồi mỗi tỉ lệ cắt giữa một ảnh.
    """
    views = [cv2.rotate(image_array, _ROTATE_CODES[angle]) for angle in augment.rotations]
    height, width = image_array.shape[:2]
    for ratio in augment.center_crops:
        crop_h, crop_w = max(1, round(height * ratio)), max(1, round(width * ratio))
        top, left = (height - crop_h) // 2, (width - crop_w) // 2
        views.append(image_array[top : top + crop_h, left : left + crop_w].copy())
    return views


def views_per_image(augment: GalleryAugmentSection, image_count: int) -> int:
    """Số vector mỗi ảnh gốc của một SKU có ``image_count`` ảnh (1 = không augment)."""
    if not augment.applies_to(image_count):
        return 1
    return 1 + len(augment.rotations) + len(augment.center_crops)


class GalleryIndexBuilder:
    """Builds a FAISS index from the product gallery."""

    def __init__(
        self, config: AppConfig, backend: EmbeddingBackend, catalog: BaseCatalogRepository | None = None
    ) -> None:
        """Initializes the gallery index builder.

        Args:
            config: Fully validated application configuration.
            backend: Initialized embedding backend used to generate
                gallery embeddings.
            catalog: Nguồn ánh xạ thư mục -> product_id; None thì mở theo ``catalog.source``
                lúc build (sau bước metadata/sync).
        """
        self._app_config = config
        self._catalog = catalog
        self._config = config.retrieval
        self._backend = backend
        self._gallery_dir = config.resolve_path(config.paths.gallery_dir)
        self._index_path = config.resolve_path(self._config.gallery_index_path)
        self._metadata_path = config.resolve_path(self._config.gallery_metadata_path)
        self._embedding_dim = self._config.embedding_dim

    # =========================================================================
    # Public API
    # =========================================================================

    def build(self) -> tuple[faiss.Index, list[dict]]:
        """Builds and saves the gallery FAISS index.

        Returns:
            Tuple of (FAISS index, vector-to-product metadata).

        Raises:
            FileNotFoundError: If the gallery or the catalog source is missing.
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
        augment = self._config.augment

        for product_dir in product_dirs:
            product_id = folder_to_product_id[product_dir.name]
            image_paths = list_image_files(product_dir)
            augmented = augment.applies_to(len(image_paths))
            logger.info(
                "Product ID %s | folder='%s' | images=%d | augmented=%s",
                product_id, product_dir.name, len(image_paths), augmented,
            )

            for image_path in image_paths:
                processed += 1
                if processed == 1 or processed % 20 == 0 or processed == total_images:
                    progress = processed * 100.0 / total_images if total_images else 100.0
                    logger.info("Embedding image %d/%d (%.1f%%): %s", processed, total_images, progress, image_path.name)

                try:
                    image_array = load_image_bgr(image_path)
                    views = [image_array] + (augment_views(image_array, augment) if augmented else [])
                    embeddings = [self._embed(view) for view in views]
                except (ValueError, RuntimeError):
                    skipped += 1
                    logger.exception("Failed to process gallery image '%s'; skipping.", image_path)
                    continue

                for embedding in embeddings:
                    vectors.append(embedding)
                    metadata.append({"product_id": product_id})
                    vector_counts_by_product[product_id] += 1

        if skipped:
            logger.warning("Skipped %d gallery image(s) due to processing errors.", skipped)

        index = self._create_index(vectors)
        self._validate_index_metadata_consistency(index, metadata)
        self._validate_vector_counts(product_dirs, folder_to_product_id, vector_counts_by_product, augment)

        self._save_index(index)
        self._save_metadata(metadata)
        fp_path = write_fingerprint(self._app_config, compute_fingerprint(self._app_config, folder_to_product_id))
        logger.info("Saved gallery fingerprint to '%s'.", fp_path)

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
        """Ánh xạ thư mục gallery -> product_id lấy từ catalog repository (SKU đang hoạt động).

        Raises:
            ValueError: Nếu catalog không có SKU nào gắn thư mục gallery.
        """
        catalog = self._catalog if self._catalog is not None else open_catalog_repository(self._app_config)
        folder_to_product_id = catalog.folder_to_product_id()
        if not folder_to_product_id:
            raise ValueError("Catalog không có SKU nào gắn thư mục gallery; không thể build index.")
        logger.info("Loaded %d stable product ID mapping(s).", len(folder_to_product_id))
        return folder_to_product_id

    @staticmethod
    def _validate_product_mapping(product_dirs: list[Path], folder_to_product_id: dict[str, str]) -> None:
        """Validates gallery folders against the catalog folder mapping (both directions)."""
        gallery_folders = {path.name for path in product_dirs}
        mapped_folders = set(folder_to_product_id.keys())

        missing_from_mapping = gallery_folders - mapped_folders
        if missing_from_mapping:
            for folder in sorted(missing_from_mapping):
                logger.error("Missing product ID mapping: '%s'", folder)
            raise ValueError(
                "Gallery contains folder(s) not registered in the catalog. Build aborted."
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
        augment: GalleryAugmentSection,
    ) -> None:
        """Validates that each product received the expected number of vectors."""
        errors: list[str] = []
        for product_dir in product_dirs:
            product_id = folder_to_product_id[product_dir.name]
            image_count = len(list_image_files(product_dir))
            expected = image_count * views_per_image(augment, image_count)
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
