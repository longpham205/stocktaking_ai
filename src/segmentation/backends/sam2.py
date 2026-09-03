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
        height, width = rgb_array.shape[:2]

        # Clip the prompt box to image bounds. Detections near an edge
        # (or from an overlap group) can carry coordinates that slightly
        # exceed the frame, which is an invalid prompt for SAM2.
        box_prompt = np.array(
            [
                max(0.0, bbox.x1),
                max(0.0, bbox.y1),
                min(float(width), bbox.x2),
                min(float(height), bbox.y2),
            ],
            dtype=np.float32,
        )

        with self._torch.inference_mode():
            self._predictor.set_image(rgb_array)
            # multimask_output=True: a box prompt inside an overlap region
            # (the only scenario Refiner ever calls this backend for) is an
            # inherently ambiguous prompt -- the box may cover parts of two
            # neighboring products. Requesting a single mask forces SAM2 to
            # commit to one interpretation with no way to compare
            # alternatives, which is precisely how refinement ends up
            # latching onto the wrong neighboring object. Requesting all
            # three candidates lets us pick the one that best matches the
            # original detector box instead of blindly trusting whichever
            # single mask SAM2 happened to return.
            masks, scores, _ = self._predictor.predict(box=box_prompt, multimask_output=True)

        if masks is None or len(masks) == 0:
            return self._fallback(bbox, detection_index)

        best_index = self._select_best_mask(masks, scores, bbox)
        mask = masks[best_index] >= self._config.mask_threshold
        mask_area = float(mask.sum())
        detection_area = max(bbox.area, 1.0)

        # Coverage ratio is measured against the ORIGINAL DETECTION BBOX
        # area, not the full source image. This must stay consistent with
        # RefinedBox.mask_area_ratio's own definition and with
        # refinement.output.min_mask_coverage_ratio, which both compare
        # against the detection box. (Previously this filter compared
        # mask_area against a fraction of the whole image, an unrelated
        # and effectively uncontrolled threshold.)
        mask_coverage_ratio = mask_area / detection_area
        if mask_coverage_ratio < self._config.min_mask_area_ratio:
            return self._fallback(bbox, detection_index)

        refined_bbox = self._mask_to_bbox(mask)
        if refined_bbox is None:
            return self._fallback(bbox, detection_index)

        return RefinedBox(
            detection_index=detection_index,
            refined_bbox=refined_bbox,
            mask_area_ratio=mask_coverage_ratio,
            refinement_confidence=float(scores[best_index]) if scores is not None and len(scores) else 0.0,
            backend="sam2",
            used_fallback=False,
        )

    @staticmethod
    def _select_best_mask(
        masks: np.ndarray, scores: np.ndarray, bbox: BoundingBox
    ) -> int:
        """Selects the best candidate among SAM2's multi-mask output.

        Prefers the candidate whose derived bbox best overlaps the
        original detector box, rather than trusting SAM2's own
        confidence score alone. This directly guards against SAM2
        latching onto a neighboring object when the box prompt sits in
        an overlap region (ambiguous prompt), since a neighboring
        object's mask can still score highly on its own merits while
        being positionally wrong.

        Args:
            masks: Array of candidate boolean masks, shape (N, H, W).
            scores: Array of SAM2's own per-mask confidence scores.
            bbox: The original detector bounding box being refined.

        Returns:
            Index of the selected candidate in `masks`/`scores`.
        """
        best_index = 0
        best_iou = -1.0
        for index in range(len(masks)):
            candidate_bbox = Sam2Backend._mask_to_bbox(masks[index] >= 0.5)
            if candidate_bbox is None:
                continue
            candidate_iou = candidate_bbox.iou(bbox)
            if candidate_iou > best_iou:
                best_index, best_iou = index, candidate_iou

        if best_iou < 0.0:
            # No candidate produced a valid bbox at all; fall back to
            # SAM2's own ranking.
            if scores is not None and len(scores):
                return int(np.argmax(scores))
            return 0

        return best_index

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