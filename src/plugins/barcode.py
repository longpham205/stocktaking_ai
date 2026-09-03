from __future__ import annotations

import cv2
import numpy as np
from pyzbar import pyzbar

from src.core.config import AppConfig
from src.core.logger import get_logger
from src.core.utils import timer
from src.models.models import CropImage

logger = get_logger(__name__)


class BarcodePlugin:
    """Decodes 1D/2D barcodes from a product crop with adaptive preprocessing."""

    name = "barcode"

    def __init__(self, config: AppConfig) -> None:
        """Initializes the barcode plugin with its configuration."""
        self._config = config.plugins.barcode
        self._quality_saturation = max(float(self._config.quality_saturation), 1e-6)
        self._type_trust = dict(self._config.type_trust)
        self._type_trust_default = float(self._config.type_trust_default)
        self._ambiguity_penalty = float(self._config.ambiguity_penalty)

        logger.info(
            "BarcodePlugin initialized (enabled=%s, preprocessing_enabled=%s, presence_check_enabled=%s)",
            self._config.enabled,
            self._config.preprocessing_enabled,
            self._config.presence_check_enabled,
        )

    def is_enabled(self) -> bool:
        """Returns whether this plugin is enabled via configuration."""
        return self._config.enabled

    # ------------------------------------------------------------------
    # PUBLIC API
    # ------------------------------------------------------------------

    def run(self, crop: CropImage) -> dict:
        """Decodes barcodes, applying preprocessing steps only as needed."""
        with timer() as elapsed:
            decoded_objects: list = []
            stage_used = "none"

            try:
                if crop.raw_image_array is not None:
                    gray = cv2.cvtColor(crop.raw_image_array, cv2.COLOR_BGR2GRAY)
                    decoded_objects, stage_used = self._decode_with_pipeline(gray)
            except Exception:  # noqa: BLE001 - zbar/cv2 backend errors vary by platform
                logger.exception("Barcode decoding failed for crop_id='%s'", crop.crop_id)
                decoded_objects, stage_used = [], "error"

            barcodes = [
                {
                    "data": obj.data.decode("utf-8", errors="ignore"),
                    "type": obj.type,
                    "quality": int(getattr(obj, "quality", 0)),
                }
                for obj in decoded_objects
            ]
            confidence = self._compute_confidence(barcodes)

        logger.info(
            "BarcodePlugin decoded %d barcode(s) for crop_id='%s' stage='%s' confidence=%.3f (%.2f ms)",
            len(barcodes),
            crop.crop_id,
            stage_used,
            confidence,
            elapsed["elapsed_ms"],
        )

        return {
            "barcodes": barcodes,
            "confidence": confidence,
            "preprocessing_stage": stage_used,
            "latency_ms": elapsed["elapsed_ms"],
        }

    # ------------------------------------------------------------------
    # PIPELINE ORCHESTRATION
    # ------------------------------------------------------------------

    def _decode_with_pipeline(self, gray: np.ndarray) -> tuple[list, str]:
        """Try decode, applying cumulative preprocessing steps on failure."""
        cfg = self._config

        # Stage -1: Edge pre-check
        if cfg.presence_check_enabled and not self._has_sufficient_edges(gray):
            return [], "no_barcode_region"

        # Stage 0: Raw decode
        result = pyzbar.decode(gray)
        if result:
            return result, "raw"

        if not cfg.preprocessing_enabled:
            return [], "none"

        # Stage 1: Barcode region detection via gradient anisotropy
        region = None
        if cfg.presence_check_enabled:
            region = self._detect_barcode_region(gray)
            if region is None:
                return [], "no_barcode_region"

        working = gray

        # Stage 2: Deskew
        if cfg.deskew_enabled:
            angle = region["angle"] if region is not None else self._estimate_skew_angle(working)
            if abs(angle) > 0.5:
                working = self._rotate_image(working, angle)
                result = pyzbar.decode(working)
                if result:
                    return result, "deskew"

        # Stage 3: Upscale
        if cfg.upscale_enabled:
            if region is not None:
                working = self._crop_to_region(working, region["bbox"])
            h, w = working.shape[:2]
            if min(h, w) < cfg.upscale_threshold:
                working = self._upscale(working)
                result = pyzbar.decode(working)
                if result:
                    return result, "upscale"

        # Stage 4: CLAHE contrast enhancement
        if cfg.clahe_enabled:
            working = self._apply_clahe(working)
            result = pyzbar.decode(working)
            if result:
                return result, "clahe"

        # Stage 5: Adaptive threshold
        if cfg.adaptive_threshold_enabled:
            thresholded = self._adaptive_threshold(working)
            result = pyzbar.decode(thresholded)
            if result:
                return result, "adaptive_threshold"

        # Stage 6: Denoise
        if cfg.denoise_enabled:
            working = self._denoise(working)
            result = pyzbar.decode(working)
            if result:
                return result, "denoise"

        # Stage 7: Sharpen
        if cfg.sharpen_enabled:
            working = self._sharpen(working)
            result = pyzbar.decode(working)
            if result:
                return result, "sharpen"

        # Stage 8: Fallback fixed-angle rotation
        if cfg.rotation_fallback_enabled:
            h, w = working.shape[:2]
            center = (w / 2, h / 2)
            for fallback_angle in cfg.rotation_fallback_angles:
                matrix = cv2.getRotationMatrix2D(center, fallback_angle, 1.0)
                rotated = cv2.warpAffine(working, matrix, (w, h))
                result = pyzbar.decode(rotated)
                if result:
                    return result, f"rotation_fallback_{fallback_angle}"

        return [], "none"

    # ------------------------------------------------------------------
    # PREPROCESSING HELPER STAGES
    # ------------------------------------------------------------------

    def _has_sufficient_edges(self, gray: np.ndarray) -> bool:
        """Reject crops with insufficient edge density."""
        edges = cv2.Canny(
            gray,
            self._config.presence_edge_canny_threshold1,
            self._config.presence_edge_canny_threshold2,
        )
        density = float(np.count_nonzero(edges)) / max(edges.size, 1)
        return density >= self._config.presence_edge_density_min

    def _detect_barcode_region(self, gray: np.ndarray) -> dict | None:
        """Locate barcode-like region via gradient anisotropy."""
        cfg = self._config

        gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=cfg.presence_sobel_ksize)
        gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=cfg.presence_sobel_ksize)
        anisotropy = cv2.absdiff(cv2.convertScaleAbs(gx), cv2.convertScaleAbs(gy))

        blurred = cv2.blur(anisotropy, (cfg.presence_blur_kernel, cfg.presence_blur_kernel))
        _, thresh = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

        kernel = cv2.getStructuringElement(
            cv2.MORPH_RECT, (cfg.presence_close_kernel_w, cfg.presence_close_kernel_h)
        )
        closed = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel)

        contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return None

        largest = max(contours, key=cv2.contourArea)
        area_ratio = cv2.contourArea(largest) / max(gray.size, 1)
        if area_ratio < cfg.presence_min_area_ratio:
            return None

        (_, _), (rw, rh), rect_angle = cv2.minAreaRect(largest)
        x, y, w, h = cv2.boundingRect(largest)

        # Normalize angle toward the long axis
        angle = rect_angle if rw < rh else rect_angle - 90.0

        return {
            "bbox": (x, y, w, h),
            "angle": float(angle),
            "area_ratio": float(area_ratio),
        }

    def _crop_to_region(self, gray: np.ndarray, bbox: tuple[int, int, int, int]) -> np.ndarray:
        """Crop to bounding box with padding, safely clamped to bounds."""
        x, y, w, h = bbox
        pad = int(self._config.presence_roi_padding)
        h_img, w_img = gray.shape[:2]

        x0, y0 = max(0, x - pad), max(0, y - pad)
        x1, y1 = min(w_img, x + w + pad), min(h_img, y + h + pad)

        cropped = gray[y0:y1, x0:x1]
        return cropped if cropped.size > 0 else gray

    def _estimate_skew_angle(self, gray: np.ndarray) -> float:
        """Estimate skew angle via Hough parallel line detection."""
        cfg = self._config
        edges = cv2.Canny(gray, cfg.deskew_canny_threshold1, cfg.deskew_canny_threshold2)
        lines = cv2.HoughLines(edges, 1, np.pi / 180, threshold=cfg.deskew_hough_threshold)

        if lines is None:
            return 0.0

        angles = []
        for rho_theta in lines[:, 0]:
            theta = rho_theta[1]
            angle = (theta * 180.0 / np.pi) - 90.0
            if -cfg.deskew_max_angle <= angle <= cfg.deskew_max_angle:
                angles.append(angle)

        return float(np.median(angles)) if angles else 0.0

    @staticmethod
    def _rotate_image(gray: np.ndarray, angle: float) -> np.ndarray:
        """Rotates image around center while filling border artifacts."""
        h, w = gray.shape[:2]
        center = (w / 2, h / 2)
        matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
        return cv2.warpAffine(
            gray,
            matrix,
            (w, h),
            flags=cv2.INTER_CUBIC,
            borderMode=cv2.BORDER_REPLICATE,
        )

    def _upscale(self, gray: np.ndarray) -> np.ndarray:
        """Upscales image up to configured max factor."""
        cfg = self._config
        h, w = gray.shape[:2]
        scale = min(cfg.upscale_threshold / max(min(h, w), 1), cfg.upscale_max_factor)
        if scale <= 1.0:
            return gray
        interp = cv2.INTER_CUBIC if cfg.upscale_interpolation == "cubic" else cv2.INTER_LINEAR
        return cv2.resize(gray, None, fx=scale, fy=scale, interpolation=interp)

    def _apply_clahe(self, gray: np.ndarray) -> np.ndarray:
        """Applies CLAHE histogram equalization."""
        cfg = self._config
        clahe = cv2.createCLAHE(
            clipLimit=cfg.clahe_clip_limit,
            tileGridSize=(cfg.clahe_tile_grid_size, cfg.clahe_tile_grid_size),
        )
        return clahe.apply(gray)

    def _adaptive_threshold(self, gray: np.ndarray) -> np.ndarray:
        """Applies Gaussian adaptive thresholding."""
        cfg = self._config
        return cv2.adaptiveThreshold(
            gray,
            255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            cfg.adaptive_threshold_block_size,
            cfg.adaptive_threshold_c,
        )

    def _denoise(self, gray: np.ndarray) -> np.ndarray:
        """Applies Fast Non-Local Means Denoising."""
        return cv2.fastNlMeansDenoising(gray, h=self._config.denoise_strength)

    def _sharpen(self, gray: np.ndarray) -> np.ndarray:
        """Sharpens image via Unsharp Masking."""
        cfg = self._config
        blurred = cv2.GaussianBlur(gray, (0, 0), cfg.sharpen_blur_sigma)
        return cv2.addWeighted(gray, cfg.sharpen_amount, blurred, 1.0 - cfg.sharpen_amount, 0)

    # ------------------------------------------------------------------
    # CONFIDENCE CALCULATION
    # ------------------------------------------------------------------

    def _compute_confidence(self, barcodes: list[dict]) -> float:
        """Computes confidence score based on decode quality, type trust, and ambiguity."""
        if not barcodes:
            return 0.0

        best = max(barcodes, key=lambda b: b["quality"])
        quality_signal = float(np.clip(best["quality"] / self._quality_saturation, 0.0, 1.0))
        type_trust = self._type_trust.get(best["type"], self._type_trust_default)

        distinct_values = {b["data"] for b in barcodes if b["data"]}
        ambiguity_penalty = 1.0 if len(distinct_values) <= 1 else self._ambiguity_penalty

        return float(np.clip(quality_signal * type_trust * ambiguity_penalty, 0.0, 1.0))