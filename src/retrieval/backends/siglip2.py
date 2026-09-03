"""SigLIP2 real neural retrieval (embedding) backend.

Uses SigLIP2 via Hugging Face transformers.
"""

from __future__ import annotations

from typing import Any

import cv2
import numpy as np
from PIL import Image

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
            "Siglip2Backend initialized (model_name='%s', device='%s', dtype='%s')",
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
            if self._config.siglip2.device == "cuda" and torch.cuda.is_available()
            else "cpu"
        )

        # FP16 significantly reduces CUDA memory usage.
        dtype = torch.float16 if device == "cuda" else torch.float32

        logger.info(
            "Loading SigLIP2 model '%s' on device='%s' dtype='%s'",
            model_name,
            device,
            dtype,
        )

        # Processor stays on CPU; model loaded with modern transformers `dtype` keyword.
        processor = AutoProcessor.from_pretrained(model_name)
        model = AutoModel.from_pretrained(model_name, dtype=dtype)
        model = model.to(device)
        model.eval()

        # Gradients are never required for retrieval inference.
        for parameter in model.parameters():
            parameter.requires_grad_(False)

        if device == "cuda":
            allocated = torch.cuda.memory_allocated() / 1024**3
            reserved = torch.cuda.memory_reserved() / 1024**3
            logger.info(
                "SigLIP2 CUDA memory after load: allocated=%.2f GB reserved=%.2f GB",
                allocated,
                reserved,
            )

        return torch, model, processor, device, dtype

    def embed(self, image_array: np.ndarray) -> np.ndarray:
        """Generate a normalized SigLIP2 image embedding."""
        if image_array is None or image_array.size == 0:
            raise ValueError("SigLIP2 received an empty image array")

        # 1. Convert BGR to RGB and resize to SigLIP2 standard size (224x224)
        rgb_array = cv2.cvtColor(image_array, cv2.COLOR_BGR2RGB)
        pil_image = Image.fromarray(rgb_array).resize(
            (224, 224), Image.Resampling.BICUBIC
        )

        # 2. Process image and validate shape
        inputs = self._processor(images=[pil_image], return_tensors="pt")
        pixel_values = inputs.get("pixel_values")

        if pixel_values is None:
            raise RuntimeError("SigLIP2 processor did not return 'pixel_values'")

        logger.info(
            "SigLIP2 input: shape=%s dtype=%s",
            tuple(pixel_values.shape),
            pixel_values.dtype,
        )

        if tuple(pixel_values.shape[-2:]) != (224, 224):
            raise RuntimeError(
                f"Unexpected SigLIP2 input size: {tuple(pixel_values.shape)}"
            )

        # 3. Transfer tensors to target device
        inputs = {k: v.to(self._device) for k, v in inputs.items()}

        logger.info(
            "SigLIP2 device input: shape=%s dtype=%s device=%s",
            tuple(inputs["pixel_values"].shape),
            inputs["pixel_values"].dtype,
            inputs["pixel_values"].device,
        )

        try:
            # 4. Inference (Model loaded in FP16/FP32 directly, no autocast needed)
            with self._torch.inference_mode():
                features = self._model.get_image_features(**inputs)

                logger.info("SigLIP2 raw output type=%s", type(features))

                # Handle transformers version compatibility
                if hasattr(features, "pooler_output"):
                    features = features.pooler_output
                elif hasattr(features, "last_hidden_state"):
                    features = features.last_hidden_state.mean(dim=1)

                logger.info(
                    "SigLIP2 tensor: shape=%s dtype=%s device=%s",
                    getattr(features, "shape", None),
                    getattr(features, "dtype", None),
                    getattr(features, "device", None),
                )

                if not isinstance(features, self._torch.Tensor):
                    raise TypeError(
                        f"Unexpected SigLIP2 output type: {type(features)}"
                    )

                if features.ndim != 2:
                    raise RuntimeError(
                        f"Unexpected SigLIP2 feature shape: {tuple(features.shape)}"
                    )

                # 5. Normalize in FP32 for numerical stability and convert to CPU numpy array
                features = self._torch.nn.functional.normalize(
                    features.float(), p=2, dim=-1
                )

                embedding = (
                    features[0].detach().cpu().numpy().astype(np.float32)
                )

                logger.info(
                    "SigLIP2 embedding ready: shape=%s dtype=%s",
                    embedding.shape,
                    embedding.dtype,
                )

            return embedding

        finally:
            # Clean up temporary GPU references without forcing costly cuda empty_cache
            if "features" in locals():
                del features
            del inputs

            if self._device == "cuda":
                allocated = self._torch.cuda.memory_allocated() / 1024**3
                reserved = self._torch.cuda.memory_reserved() / 1024**3
                logger.debug(
                    "SigLIP2 CUDA memory after embed: allocated=%.2f GB reserved=%.2f GB",
                    allocated,
                    reserved,
                )