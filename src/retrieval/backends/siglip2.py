"""SigLIP2 real neural retrieval (embedding) backend.

Uses SigLIP2 (https://huggingface.co/docs/transformers/model_doc/siglip2)
via Hugging Face `transformers`. Selected via
`configs/config.yaml -> retrieval.backend: "siglip2"`.

Requires: `pip install torch transformers`, plus network access to
Hugging Face Hub to download the checkpoint (`retrieval.siglip2.model_name`)
on first use.
"""

from __future__ import annotations

from typing import Any

import cv2
import numpy as np

from src.core.config import RetrievalSection
from src.core.logger import get_logger
from src.retrieval.backends.base import EmbeddingBackend

logger = get_logger(__name__)


class Siglip2Backend(EmbeddingBackend):
    """Real neural image embedding using SigLIP2."""

    def __init__(self, config: RetrievalSection) -> None:
        """Loads the SigLIP2 model and processor exactly once.

        Args:
            config: The `retrieval` section of the application configuration.

        Raises:
            ImportError: If `torch`/`transformers` are not installed.
        """
        self._config = config
        self._torch, self._model, self._processor, self._device = self._load_model()
        logger.info(
            "Siglip2Backend initialized (model_name='%s' device='%s')",
            config.siglip2.model_name,
            self._device,
        )

    def _load_model(self) -> tuple[Any, Any, Any, str]:
        """Loads the SigLIP2 model, its processor, and resolves the device.

        Returns:
            Tuple of (torch module, loaded model, loaded processor, device string).

        Raises:
            ImportError: If `torch`/`transformers` are not installed.
        """
        try:
            import torch
            from transformers import AutoModel, AutoProcessor
        except ImportError as exc:  # pragma: no cover - exercised only without optional deps
            raise ImportError(
                "retrieval.backend='siglip2' requires the optional dependencies "
                "'torch' and 'transformers'. Install them with: "
                "pip install torch transformers"
            ) from exc

        model_name = self._config.siglip2.model_name
        device = "cuda" if self._config.siglip2.device == "cuda" and torch.cuda.is_available() else "cpu"

        logger.info("Loading SigLIP2 model '%s' on device='%s'", model_name, device)
        processor = AutoProcessor.from_pretrained(model_name)
        model = AutoModel.from_pretrained(model_name).to(device).eval()

        return torch, model, processor, device

    def embed(self, image_array: np.ndarray) -> np.ndarray:
        from PIL import Image

        rgb_array = cv2.cvtColor(image_array, cv2.COLOR_BGR2RGB)
        pil_image = Image.fromarray(rgb_array)

        inputs = self._processor(
            images=[pil_image],
            return_tensors="pt",
        ).to(self._device)

        with self._torch.no_grad():
            features = self._model.get_image_features(**inputs)
        # BaseModelOutputWithPooling
        if hasattr(features, "pooler_output"):
            features = features.pooler_output
        elif hasattr(features, "last_hidden_state"):
            features = features.last_hidden_state.mean(dim=1)
        if not isinstance(features, self._torch.Tensor):
            raise TypeError(
                f"Unexpected SigLIP2 output type: {type(features)}"
            )
        features = self._torch.nn.functional.normalize(
            features,
            p=2,
            dim=-1,
        )
        return (
            features[0]
            .detach()
            .cpu()
            .numpy()
            .astype(np.float32)
        )
