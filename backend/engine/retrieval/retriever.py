"""Visual embedding and gallery retrieval module.

Responsibility:
    - Load exactly one configured EmbeddingBackend.
    - Load a pre-built FAISS gallery index (built offline by BuildPipeline
      -> GalleryIndexBuilder; never rebuilt here).
    - Generate an embedding for a cropped product image.
    - Search the gallery index for visually similar products.
    - Resolve product display information from the product catalog.

This module must NEVER perform object detection, image cropping, OCR,
barcode decoding, or interact directly with Storage/UI (see
02_MODULE_SPECIFICATION.md, Section 4).

Identity convention:
    Every "product_id" surfaced by this module (in RetrievalCandidate,
    gallery_metadata.json entries, catalog entries) is the stable
    **internal numeric ID as a string** (e.g. "1", "9", "20") — never the
    gallery folder name. IDs come from the catalog DB (engine/catalog,
    CatalogRepository); they equal the COCO category_id and never change.

Runtime flow:

    CropImage -> EmbeddingBackend -> query embedding -> FAISS search
        -> product_id -> products.json -> RetrievalResult

Backend note: `configs/config.yaml -> retrieval.backend` supports
"mock_visual_embedding" (default, no weights required) and "siglip2"
(real neural backend). See `engine/retrieval/backends/`.
"""

from __future__ import annotations

import json

import faiss
import numpy as np

from engine.catalog.factory import open_catalog_repository
from engine.catalog.repository import BaseCatalogRepository
from engine.core.config import AppConfig
from engine.core.logger import get_logger
from engine.core.utils import timer
from engine.models.models import CropImage, RetrievalCandidate, RetrievalResult
from engine.retrieval.backends.base import EmbeddingBackend
from engine.retrieval.fingerprint import compute_fingerprint, fingerprint_path, index_is_current

logger = get_logger(__name__)


class Retriever:
    """Generates query embeddings and searches the pre-built gallery index."""

    def __init__(self, config: AppConfig, catalog: BaseCatalogRepository | None = None) -> None:
        """Initializes the Retriever.

        Loads the configured embedding backend, the pre-built FAISS
        gallery index, the vector-to-product mapping, and the product
        catalog, all exactly once.

        Args:
            config: Fully validated application configuration.
            catalog: Catalog repository dùng chung; None thì mở theo ``catalog.source``.

        Raises:
            FileNotFoundError: If the required FAISS index, gallery
                metadata, or product catalog is missing (run the build
                pipeline first).
            ValueError: If the configured embedding backend is unsupported.
        """
        self._config = config.retrieval
        self._index_path = config.resolve_path(self._config.gallery_index_path)
        self._metadata_path = config.resolve_path(self._config.gallery_metadata_path)
        self._embedding_dim = self._config.embedding_dim

        self._backend = self._load_backend()
        self._index = self._load_index()
        self._metadata = self._load_gallery_metadata()
        self._catalog = catalog if catalog is not None else open_catalog_repository(config)
        logger.info("Loaded %d product(s) from catalog (source=%s).", len(self._catalog.products()), config.catalog.source)
        self._check_index_current(config)

        logger.info(
            "Retriever initialized with backend='%s' gallery_size=%d embedding_dim=%d",
            self._config.backend,
            self._index.ntotal,
            self._embedding_dim,
        )

    # ------------------------------------------------------------------
    # Backend
    # ------------------------------------------------------------------

    def _load_backend(self) -> EmbeddingBackend:
        """Instantiates the configured embedding backend exactly once.

        Returns:
            An initialized EmbeddingBackend.

        Raises:
            ValueError: If the configured backend is unsupported.
            ImportError: If the backend's optional dependencies are missing.
        """
        backend_name = self._config.backend

        if backend_name == "mock_visual_embedding":
            from engine.retrieval.backends.mock_visual_embedding import MockVisualEmbeddingBackend

            return MockVisualEmbeddingBackend(self._config)

        if backend_name == "siglip2":
            from engine.retrieval.backends.siglip2 import Siglip2Backend

            return Siglip2Backend(self._config)

        raise ValueError(
            f"Unsupported retrieval backend: '{backend_name}'. "
            "Available backends: 'mock_visual_embedding', 'siglip2'. To add "
            "a new one, implement engine/retrieval/backends/<name>.py and "
            "register it in Retriever._load_backend."
        )

    def _embed(self, image_array: np.ndarray) -> np.ndarray:
        """Computes a fixed-length, L2-normalized visual embedding.

        Args:
            image_array: BGR image pixel array (crop or gallery image).

        Returns:
            A 1-D float32 embedding vector of length `embedding_dim`.
        """
        embedding = np.asarray(self._backend.embed(image_array), dtype=np.float32).reshape(-1)

        if embedding.shape[0] < self._embedding_dim:
            embedding = np.pad(embedding, (0, self._embedding_dim - embedding.shape[0]))
        else:
            embedding = embedding[: self._embedding_dim]

        norm = np.linalg.norm(embedding)
        if norm > 1e-8:
            embedding = embedding / norm
        return embedding.astype(np.float32)

    # ------------------------------------------------------------------
    # Load persistent retrieval data
    # ------------------------------------------------------------------

    def _load_index(self) -> faiss.Index:
        """Loads the pre-built FAISS gallery index.

        Returns:
            The loaded FAISS index.

        Raises:
            FileNotFoundError: If the index has not been built yet.
        """
        if not self._index_path.is_file():
            raise FileNotFoundError(
                f"Gallery FAISS index was not found: {self._index_path}. "
                "Run the build pipeline before inference."
            )
        logger.info("Loading gallery FAISS index from '%s'.", self._index_path)
        return faiss.read_index(str(self._index_path))

    def _load_gallery_metadata(self) -> list[dict]:
        """Loads the FAISS vector-to-product mapping.

        Expected structure: `[{"product_id": "1"}, {"product_id": "1"}, ...]`.

        Returns:
            Metadata list indexed by FAISS vector position.

        Raises:
            FileNotFoundError: If gallery metadata does not exist.
            ValueError: If metadata is invalid or its length does not
                match the FAISS index size.
        """
        if not self._metadata_path.is_file():
            raise FileNotFoundError(
                f"Gallery metadata was not found: {self._metadata_path}. "
                "Run the build pipeline before inference."
            )

        logger.info("Loading gallery metadata from '%s'.", self._metadata_path)
        try:
            with self._metadata_path.open("r", encoding="utf-8") as file_handle:
                metadata = json.load(file_handle)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid gallery metadata JSON: {self._metadata_path}") from exc

        if not isinstance(metadata, list):
            raise ValueError("Gallery metadata must be a JSON list.")

        if len(metadata) != self._index.ntotal:
            raise ValueError(
                "Gallery metadata size does not match FAISS index size: "
                f"metadata={len(metadata)}, index={self._index.ntotal}."
            )
        return metadata

    def _check_index_current(self, config: AppConfig) -> None:
        """Refuses a FAISS index built from another gallery, catalog mapping or config.

        The index is only rebuilt offline (BuildPipeline), so after gallery photos, SKUs or
        ``retrieval.augment`` change, a stale index would otherwise be served silently.

        Raises:
            RuntimeError: If the saved fingerprint is missing or differs from the current inputs.
        """
        if not index_is_current(config, compute_fingerprint(config, self._catalog.folder_to_product_id())):
            raise RuntimeError(
                f"Gallery index '{self._index_path}' does not match the current gallery photos, catalog "
                f"or retrieval settings (fingerprint '{fingerprint_path(config)}' missing or different). "
                "Rebuild it: run the build pipeline with retrieval.build_gallery_index: true "
                "(e.g. data/experiments/move_31_32_1007/rebuild_index.py), then restart."
            )

    def _get_product_name(self, product_id: str) -> str:
        """Resolves the display name for a product_id.

        Args:
            product_id: Stable internal product ID (string).

        Returns:
            The product's display name, or the product_id itself if not
            found in the catalog (fail-soft, never raises).
        """
        product = self._catalog.products().get(product_id)
        if product is None:
            logger.warning("Product ID '%s' not found in catalog; using ID as display name.", product_id)
            return product_id
        return product.product_name

    def get_product(self, product_id: str) -> dict | None:
        """Returns the full catalog entry for a product_id, if known.

        Args:
            product_id: Stable internal product ID (string).

        Returns:
            The catalog dict (product_name, brand, category, barcode,
            description, folder, ...), or None if unknown. Used by
            Reranker to match plugin evidence (e.g. barcode) against
            catalog fields.
        """
        return self._catalog.get_product(product_id)

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    def retrieve(self, crop: CropImage) -> RetrievalResult:
        """Retrieves the Top-K most visually similar gallery products.

        Args:
            crop: The cropped product image to identify.

        Returns:
            A RetrievalResult containing ranked candidates. If the gallery
            index is empty, an empty candidate list is returned.
        """
        with timer() as elapsed:
            candidates: list[RetrievalCandidate] = []

            if self._index.ntotal > 0:
                query_vector = self._embed(crop.image_array).reshape(1, -1)
                top_k = min(self._config.top_k, self._index.ntotal)
                scores, indices = self._index.search(query_vector, top_k)

                for rank, (score, idx) in enumerate(zip(scores[0], indices[0]), start=1):
                    if idx < 0:
                        continue
                    entry = self._metadata[idx]
                    product_id = str(entry["product_id"])
                    candidates.append(
                        RetrievalCandidate(
                            product_id=product_id,
                            product_name=self._get_product_name(product_id),
                            similarity_score=float(np.clip(score, -1.0, 1.0)),
                            rank=rank,
                        )
                    )
            else:
                logger.warning("Gallery index is empty; retrieval will return no candidates.")

        logger.info(
            "Retriever returned %d candidate(s) for crop_id='%s' (%.2f ms)",
            len(candidates),
            crop.crop_id,
            elapsed["elapsed_ms"],
        )

        return RetrievalResult(
            crop_id=crop.crop_id,
            candidates=candidates,
            processing_time_ms=elapsed["elapsed_ms"],
            detection_confidence=crop.detection_confidence,
        )
