"""SigLIP2 real neural retrieval (embedding) backend.

Uses SigLIP2 via Hugging Face transformers.
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
        self._config = config
        (
            self._torch,
            self._model,
            self._processor,
            self._device,
            self._dtype,
        ) = self._load_model()

        logger.info(
            "Siglip2Backend initialized "
            "(model_name='%s' device='%s' dtype='%s')",
            config.siglip2.model_name,
            self._device,
            self._dtype,
        )

    def _load_model(self) -> tuple[Any, Any, Any, str, Any]:
        """Load SigLIP2 model and processor."""

        try:
            import torch
            from transformers import AutoModel, AutoProcessor
        except ImportError as exc:
            raise ImportError(
                "retrieval.backend='siglip2' requires the optional dependencies "
                "'torch' and 'transformers'. Install them with: "
                "pip install torch transformers"
            ) from exc

        model_name = self._config.siglip2.model_name

        device = (
            "cuda"
            if self._config.siglip2.device == "cuda"
            and torch.cuda.is_available()
            else "cpu"
        )

        # FP16 significantly reduces CUDA memory usage.
        if device == "cuda":
            dtype = torch.float16
        else:
            dtype = torch.float32

        logger.info(
            "Loading SigLIP2 model '%s' on device='%s' dtype='%s'",
            model_name,
            device,
            dtype,
        )

        processor = AutoProcessor.from_pretrained(model_name)

        model = AutoModel.from_pretrained(
            model_name,
            torch_dtype=dtype if device == "cuda" else torch.float32,
        )

        model = model.to(device)
        model.eval()

        # Make sure gradients are disabled at model level as well.
        for parameter in model.parameters():
            parameter.requires_grad_(False)

        if device == "cuda":
            allocated = torch.cuda.memory_allocated() / 1024**3
            reserved = torch.cuda.memory_reserved() / 1024**3

            logger.info(
                "SigLIP2 CUDA memory after load: "
                "allocated=%.2f GB reserved=%.2f GB",
                allocated,
                reserved,
            )

        return torch, model, processor, device, dtype

    def embed(self, image_array: np.ndarray) -> np.ndarray:
        """Generate a normalized image embedding on CPU."""

        from PIL import Image

        if image_array is None or image_array.size == 0:
            raise ValueError("SigLIP2 received an empty image array")

        rgb_array = cv2.cvtColor(image_array, cv2.COLOR_BGR2RGB)
        pil_image = Image.fromarray(rgb_array)

        # Processor runs on CPU first.
        inputs = self._processor(
            images=[pil_image],
            return_tensors="pt",
        )

        # Move only the processor tensors to the selected device.
        inputs = {
            key: value.to(self._device)
            for key, value in inputs.items()
        }

        try:
            with self._torch.inference_mode():

                if self._device == "cuda":
                    with self._torch.autocast(
                        device_type="cuda",
                        dtype=self._dtype,
                    ):
                        features = self._model.get_image_features(
                            **inputs
                        )
                else:
                    features = self._model.get_image_features(
                        **inputs
                    )

                # Handle possible model output structures.
                if hasattr(features, "pooler_output"):
                    features = features.pooler_output

                elif hasattr(features, "last_hidden_state"):
                    features = features.last_hidden_state.mean(dim=1)

                if not isinstance(features, self._torch.Tensor):
                    raise TypeError(
                        f"Unexpected SigLIP2 output type: {type(features)}"
                    )

                features = self._torch.nn.functional.normalize(
                    features.float(),
                    p=2,
                    dim=-1,
                )

                # IMPORTANT:
                # Move the final embedding to CPU immediately.
                embedding = (
                    features[0]
                    .detach()
                    .cpu()
                    .numpy()
                    .astype(np.float32)
                )

            return embedding

        finally:
            # Explicitly release GPU references after every crop.
            del inputs

            if "features" in locals():
                del features

            if self._device == "cuda":
                self._torch.cuda.empty_cache()