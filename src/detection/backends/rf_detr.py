"""RF-DETR real neural detection backend.

Uses RF-DETR (https://github.com/roboflow/rf-detr), Roboflow's real-time
DETR-based object detector, pretrained on COCO. Selected via
`configs/config.yaml -> detection.backend: "rf_detr"`.

Requires: `pip install rfdetr torch torchvision supervision`, plus network
access to download pretrained weights on first use (cached under
`~/.roboflow/models/` by the `rfdetr` package itself, or loaded from
`detection.rf_detr.weights_path` if a fine-tuned checkpoint is configured).
"""

from __future__ import annotations

from typing import Any

import cv2
import numpy as np
import torch

from src.core.config import DetectionSection
from src.core.logger import get_logger
from src.core.utils import clip_coordinate
from src.detection.backends.base import DetectionBackend
from src.models.models import BoundingBox, Detection

logger = get_logger(__name__)

_VARIANT_CLASSES: dict[str, str] = {
    "nano": "RFDETRNano",
    "small": "RFDETRSmall",
    "medium": "RFDETRMedium",
    "base": "RFDETRBase",
    "large": "RFDETRLarge",
}


class RfDetrBackend(DetectionBackend):
    """Real neural object detector using RF-DETR."""

    def __init__(self, config: DetectionSection) -> None:
        """Loads the RF-DETR model exactly once.

        Args:
            config: The `detection` section of the application configuration.

        Raises:
            ImportError: If `rfdetr` (and its `torch`/`supervision`
                dependencies) is not installed.
        """
        self._config = config
        self._model= self._load_model()
        logger.info(
            "RfDetrBackend initialized (variant='%s' weights_path='%s')",
            config.rf_detr.variant,
            config.rf_detr.weights_path or "<coco-pretrained>",
        )

    def _load_model(self) -> Any:
        """Loads the RF-DETR model instance and its COCO class-name table.

        Returns:
            Tuple of (RF-DETR model instance, {class_id: class_name} map).

        Raises:
            ImportError: If `rfdetr` is not installed.
        """
        try:
            import rfdetr
        except ImportError as exc:  # pragma: no cover - exercised only without optional deps
            raise ImportError(
                "detection.backend='rf_detr' requires the optional dependencies "
                "'rfdetr', 'torch', and 'supervision'. Install them with: "
                "pip install rfdetr torch torchvision supervision"
            ) from exc

        variant = self._config.rf_detr.variant.lower()
        class_name = _VARIANT_CLASSES.get(variant, "RFDETRBase")
        model_cls = getattr(rfdetr, class_name, None) or getattr(rfdetr, "RFDETRBase")

        model_kwargs: dict[str, Any] = {}
        if self._config.rf_detr.weights_path:
            model_kwargs["pretrain_weights"] = self._config.rf_detr.weights_path

        logger.info("Loading RF-DETR model class='%s' kwargs=%s", model_cls.__name__, model_kwargs)
        model = model_cls(**model_kwargs)
        infer_cfg = self._config.rf_detr.inference
        
        infer_cfg = self._config.rf_detr.inference
        
        dtype = (
            torch.float16
            if infer_cfg.dtype.lower() == "float16"
            else torch.float32
        )

        if infer_cfg.optimize:
            dtype = getattr(torch, infer_cfg.dtype)

            logger.info(
                "Optimizing RF-DETR for inference "
                "(dtype=%s compile=%s batch_size=%d)",
                infer_cfg.dtype,
                infer_cfg.compile,
                infer_cfg.batch_size,
            )

            model.inference(
                compile=infer_cfg.compile,
                batch_size=infer_cfg.batch_size,
                dtype=dtype,
                inplace=infer_cfg.inplace,
            )
        return model

    def detect(self, image_array: np.ndarray) -> list[Detection]:
        """Runs real RF-DETR neural inference on a full BGR source image.

        Args:
            image_array: BGR pixel array of the full source image.

        Returns:
            List of Detection objects above the configured confidence
            threshold, with COCO class names resolved from the model's
            predicted class IDs.
        """
        from PIL import Image

        height, width = image_array.shape[:2]
        rgb_array = cv2.cvtColor(image_array, cv2.COLOR_BGR2RGB)
        pil_image = Image.fromarray(rgb_array)

        sv_detections = self._model.predict(pil_image, threshold=self._config.confidence_threshold)

        detections: list[Detection] = []
        for xyxy, confidence, class_id in zip(
            sv_detections.xyxy, sv_detections.confidence, sv_detections.class_id
        ):
            x1, y1, x2, y2 = xyxy
            bbox = BoundingBox(
                x1=clip_coordinate(float(x1), 0, width),
                y1=clip_coordinate(float(y1), 0, height),
                x2=clip_coordinate(float(x2), 0, width),
                y2=clip_coordinate(float(y2), 0, height),
            )
            class_name = "object"
            detections.append(
                Detection(
                    bbox=bbox,
                    confidence=float(confidence),
                    class_id=int(class_id),
                    class_name=class_name,
                )
            )

        return detections
