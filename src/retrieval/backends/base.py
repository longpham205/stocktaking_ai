"""Abstract interface shared by every retrieval (embedding) backend.

Adding a new embedding model (e.g. CLIP, a fine-tuned SigLIP2 checkpoint,
a different vision encoder) means: (1) create a new file in this package
that implements `EmbeddingBackend`, and (2) register its name in
`Retriever._load_backend` (src/retrieval/retriever.py). No other file
needs to change — `Retriever.retrieve(crop) -> RetrievalResult` and every
downstream module (DecisionEngine, Pipeline, ...) stay exactly the same
(03_DEVELOPMENT_RULES.md, Rule 25 - Extensibility Standard).
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np


class EmbeddingBackend(ABC):
    """Base interface every retrieval embedding backend must implement."""

    @abstractmethod
    def embed(self, image_array: np.ndarray) -> np.ndarray:
        """Computes a raw visual embedding for a single BGR image.

        Implementations should load their weights once in `__init__` (see
        03_DEVELOPMENT_RULES.md, Rule 23) and must NOT reload them here.

        Args:
            image_array: BGR pixel array (a product crop, or a gallery
                reference image — both are embedded through this same
                method).

        Returns:
            A 1-D float32 embedding vector. Padding/truncating to the
            configured `embedding_dim` and L2-normalizing is the caller's
            (`Retriever`) responsibility, not the backend's, so every
            backend's output is treated uniformly by the FAISS index.
        """
        raise NotImplementedError
