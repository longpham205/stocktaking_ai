"""SAM2 real neural segmentation/refinement backend.

Uses Meta's Segment Anything Model 2
(https://github.com/facebookresearch/sam2) with box prompts derived from
the Detector's bounding boxes, to produce a tighter/adjusted bounding box
for detections flagged as suspicious by ``OverlapResolver``.

Requires: `pip install sam2 torch torchvision`, plus a local checkpoint
file (see `configs/config.yaml -> refinement.sam2.checkpoint_path`) and
its matching model config (`refinement.sam2.model_type` /
`refinement.sam2.model_config`).
"""

from __future__ import annotations

from typing import Any

import cv2
import numpy as np

from src.core.config import Sam2Section
from src.core.logger import get_logger
from src.models.models import BoundingBox, RefinedBox
from pathlib import Path
from urllib.request import urlopen

logger = get_logger(__name__)

# Maps our short config.sam2.model_type values to the SAM2 repo's
# packaged model config YAML paths (as of the sam2 / sam2.1 releases).
_MODEL_TYPE_TO_CONFIG: dict[str, str] = {
    "sam2.1_hiera_tiny": "configs/sam2.1/sam2.1_hiera_t.yaml",
    "sam2.1_hiera_small": "configs/sam2.1/sam2.1_hiera_s.yaml",
    "sam2.1_hiera_base_plus": "configs/sam2.1/sam2.1_hiera_b+.yaml",
    "sam2.1_hiera_large": "configs/sam2.1/sam2.1_hiera_l.yaml",
}

_CHECKPOINT_URLS: dict[str, str] = {
    "sam2.1_hiera_tiny": (
        "https://dl.fbaipublicfiles.com/segment_anything_2/"
        "092824/sam2.1_hiera_tiny.pt"
    ),
    "sam2.1_hiera_small": (
        "https://dl.fbaipublicfiles.com/segment_anything_2/"
        "092824/sam2.1_hiera_small.pt"
    ),
    "sam2.1_hiera_base_plus": (
        "https://dl.fbaipublicfiles.com/segment_anything_2/"
        "092824/sam2.1_hiera_base_plus.pt"
    ),
    "sam2.1_hiera_large": (
        "https://dl.fbaipublicfiles.com/segment_anything_2/"
        "092824/sam2.1_hiera_large.pt"
    ),
}


class Sam2Backend:
    """Real neural segmentation/refinement backend using SAM2."""

    def __init__(self, config: Sam2Section) -> None:
        """Loads the SAM2 predictor exactly once.

        Args:
            config: The `refinement.sam2` section of the application
                configuration.

        Raises:
            ImportError: If `sam2`/`torch` are not installed.
            FileNotFoundError: If the checkpoint file does not exist.
        """
        self._config = config
        self._torch, self._predictor = self._load_model()
        logger.info(
            "Sam2Backend initialized (model_type='%s' device='%s')",
            config.model_type,
            config.device,
        )
        
        
    def _ensure_checkpoint(self) -> Path:
        """Ensure the configured SAM2 checkpoint exists locally.

        If the checkpoint is missing, download the official SAM2.1
        checkpoint corresponding to ``model_type`` into the project's
        configured checkpoint path.

        Returns:
            Path to the local checkpoint.

        Raises:
            ValueError: If the configured model type has no known checkpoint URL.
            RuntimeError: If the checkpoint download fails.
        """

        checkpoint_path = Path(self._config.checkpoint_path)

        if checkpoint_path.is_file():
            logger.info(
                "SAM2 checkpoint found locally: '%s'",
                checkpoint_path,
            )
            return checkpoint_path

        model_type = self._config.model_type.lower()

        checkpoint_url = _CHECKPOINT_URLS.get(model_type)

        if not checkpoint_url:
            raise ValueError(
                f"Cannot automatically download SAM2 checkpoint for "
                f"model_type='{self._config.model_type}'. "
                "Provide a valid local refinement.sam2.checkpoint_path."
            )

        checkpoint_path.parent.mkdir(parents=True, exist_ok=True)

        logger.info(
            "SAM2 checkpoint not found. Downloading model_type='%s' "
            "from '%s'",
            model_type,
            checkpoint_url,
        )

        temporary_path = checkpoint_path.with_suffix(
            checkpoint_path.suffix + ".download"
        )

        try:
            with urlopen(checkpoint_url, timeout=60) as response:
                total_size = int(
                    response.headers.get("Content-Length", 0)
                )

                downloaded = 0

                with temporary_path.open("wb") as file:
                    while True:
                        chunk = response.read(1024 * 1024)

                        if not chunk:
                            break

                        file.write(chunk)
                        downloaded += len(chunk)

                        if total_size > 0:
                            percent = downloaded * 100 / total_size

                            logger.info(
                                "Downloading SAM2 checkpoint: %.1f%% (%d / %d MB)",
                                percent,
                                downloaded // (1024 * 1024),
                                total_size // (1024 * 1024),
                            )

            temporary_path.replace(checkpoint_path)

        except Exception as exc:
            if temporary_path.exists():
                temporary_path.unlink()

            raise RuntimeError(
                "Failed to download SAM2 checkpoint. "
                f"URL='{checkpoint_url}'. "
                f"Destination='{checkpoint_path}'. "
                "Check your network connection or download the "
                "checkpoint manually."
            ) from exc

        if not checkpoint_path.is_file():
            raise RuntimeError(
                f"SAM2 checkpoint download completed but the file "
                f"does not exist: '{checkpoint_path}'."
            )

        logger.info(
            "SAM2 checkpoint downloaded successfully: '%s'",
            checkpoint_path,
        )

        return checkpoint_path

    def _load_model(self) -> tuple[Any, Any]:
        """Loads the SAM2 image predictor.

        Returns:
            Tuple of (torch module, SAM2ImagePredictor instance).

        Raises:
            ImportError: If `sam2`/`torch` are not installed.
            ValueError: If the configured model type is invalid.
            RuntimeError: If the checkpoint cannot be downloaded.
        """

        try:
            import torch
            from sam2.build_sam import build_sam2
            from sam2.sam2_image_predictor import SAM2ImagePredictor
        except ImportError as exc:  # pragma: no cover
            raise ImportError(
                "refinement.backend='sam2' requires the optional dependencies "
                "'sam2' and 'torch'. Install them with: "
                "pip install torch torchvision sam2"
            ) from exc

        checkpoint_path = self._ensure_checkpoint()

        model_config = (
            self._config.model_config
            or _MODEL_TYPE_TO_CONFIG.get(self._config.model_type)
        )

        if not model_config:
            raise ValueError(
                f"Unknown SAM2 model_type '{self._config.model_type}' "
                "and no explicit refinement.sam2.model_config was provided."
            )

        device = (
            "cuda"
            if self._config.device == "cuda"
            and torch.cuda.is_available()
            else "cpu"
        )

        logger.info(
            "Loading SAM2 model_config='%s' checkpoint='%s' device='%s'",
            model_config,
            checkpoint_path,
            device,
        )

        sam2_model = build_sam2(
            model_config,
            str(checkpoint_path),
            device=device,
        )

        predictor = SAM2ImagePredictor(sam2_model)

        return torch, predictor

    def refine(
        self,
        image_array: np.ndarray,
        bbox: BoundingBox,
        detection_index: int,
    ) -> RefinedBox:
        """Refines one detection's bounding box using SAM2's box prompt.

        Args:
            image_array: BGR pixel array of the full source image.
            bbox: The original detector bounding box to refine.
            detection_index: Index into the originating DetectionResult.

        Returns:
            A RefinedBox with the mask-derived tighter bbox, or a
            fallback RefinedBox (`used_fallback=True`, `refined_bbox`
            equal to `bbox`) if SAM2 produced an empty/invalid mask.
        """
        rgb_array = cv2.cvtColor(image_array, cv2.COLOR_BGR2RGB)
        box_prompt = np.array([bbox.x1, bbox.y1, bbox.x2, bbox.y2], dtype=np.float32)

        with self._torch.inference_mode():
            self._predictor.set_image(rgb_array)
            masks, scores, _ = self._predictor.predict(box=box_prompt, multimask_output=False)

        if masks is None or len(masks) == 0:
            return self._fallback(bbox, detection_index)

        mask = masks[0] >= self._config.mask_threshold
        mask_area = float(mask.sum())
        detection_area = max(bbox.area, 1.0)

        min_area_pixels = self._config.min_mask_area_ratio * image_array.shape[0] * image_array.shape[1]
        if mask_area < min_area_pixels:
            return self._fallback(bbox, detection_index)

        refined_bbox = self._mask_to_bbox(mask)
        if refined_bbox is None:
            return self._fallback(bbox, detection_index)

        return RefinedBox(
            detection_index=detection_index,
            refined_bbox=refined_bbox,
            mask_area_ratio=mask_area / detection_area,
            refinement_confidence=float(scores[0]) if scores is not None and len(scores) else 0.0,
            backend="sam2",
            used_fallback=False,
        )

    @staticmethod
    def _mask_to_bbox(mask: np.ndarray) -> BoundingBox | None:
        """Converts a boolean mask into its tight bounding box.

        Args:
            mask: 2-D boolean array, True where the mask is foreground.

        Returns:
            The tight BoundingBox around the mask, or None if the mask
            has no foreground pixels.
        """
        ys, xs = np.where(mask)
        if ys.size == 0 or xs.size == 0:
            return None
        return BoundingBox(
            x1=float(xs.min()),
            y1=float(ys.min()),
            x2=float(xs.max() + 1),
            y2=float(ys.max() + 1),
        )

    @staticmethod
    def _fallback(bbox: BoundingBox, detection_index: int) -> RefinedBox:
        """Builds a fallback RefinedBox equal to the original detection bbox.

        Args:
            bbox: The original detector bounding box.
            detection_index: Index into the originating DetectionResult.

        Returns:
            A RefinedBox with `used_fallback=True`.
        """
        return RefinedBox(
            detection_index=detection_index,
            refined_bbox=bbox,
            mask_area_ratio=0.0,
            refinement_confidence=0.0,
            backend="sam2",
            used_fallback=True,
        )
