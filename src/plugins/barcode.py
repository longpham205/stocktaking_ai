"""Barcode decoding plugin.

Decodes 1D/2D barcodes present within a product crop via `pyzbar`. Runs
on `crop.raw_image_array` (original resolution) rather than
`crop.image_array` — barcodes become unreadable once downsized to the
retrieval crop size.

Pipeline (applied cumulatively, early-exit on first successful decode):
    -1. Presence pre-check: cheap edge-density gate that rejects crops
        with almost no edge structure at all before any decode attempt.
    0.  Raw decode — succeeds for most well-framed crops.
    1.  Barcode region detection via gradient anisotropy: barcode bars
        produce strong, spatially-dense gradient energy along one axis
        and weak energy along the perpendicular axis, regardless of
        rotation — a signature text and flat regions don't share. If no
        such region is found after step 0 fails, the crop very likely
        has no barcode, so the expensive preprocessing tail (CLAHE,
        threshold, denoise, sharpen, rotation fallback) is skipped
        entirely.
    2.  Deskew, seeded from the detected region's angle when available
        (falls back to a dedicated Hough pass otherwise) — barcode bars
        are a strong parallel-line pattern, so this recovers arbitrary
        skew angles, not just 90° multiples.
    3.  Upscale small crops, cropped to the detected region first (when
        available) so resolution isn't spent on irrelevant background.
    4.  CLAHE contrast enhancement.
    5.  Adaptive threshold binarization.
    6.  Denoise.
    7.  Sharpen.
    8.  Fallback: fixed-angle rotation (90/180/270) — last resort for
        cases the region/Hough estimate missed or misjudged.

Barcode matching (see Reranker):
    Decoded values and catalog values are both normalized before
    comparison (digits only, UPC-A aligned to EAN-13/JAN by prepending
    a leading zero) since the two encode the same identifier and
    catalog/decoder data commonly mixes the two forms.
"""

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
        """Initializes the barcode plugin with its configuration.

        Args:
            config: Fully validated application configuration.
        """
        self._config = config.plugins.barcode

        self._quality_saturation = max(float(self._config.quality_saturation), 1e-6)
        self._type_trust = dict(self._config.type_trust)
        self._type_trust_default = float(self._config.type_trust_default)
        self._ambiguity_penalty = float(self._config.ambiguity_penalty)

        logger.info(
            "BarcodePlugin initialized (enabled=%s, preprocessing_enabled=%s, "
            "presence_check_enabled=%s)",
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
        """Decodes barcodes, applying preprocessing steps only as needed.

        Args:
            crop: The cropped product image to analyze.

        Returns:
            A dictionary with keys:
                barcodes: List of {"data": str, "type": str, "quality": int}.
                confidence: Plugin confidence in [0, 1] — how much this
                    plugin's decode result should be trusted, independent
                    of whether it matches any specific catalog candidate.
                preprocessing_stage: Name of the stage that produced a
                    result, "no_barcode_region" if the presence check
                    rejected the crop, or "none" if every stage failed.
        """
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
            "BarcodePlugin decoded %d barcode(s) for crop_id='%s' "
            "stage='%s' confidence=%.3f (%.2f ms)",
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

        # Stage -1: cheapest possible gate — reject crops with almost no
        # edge structure before spending anything else on them.
        if cfg.presence_check_enabled and not self._has_sufficient_edges(gray):
            return [], "no_barcode_region"

        # Stage 0: raw decode
        result = pyzbar.decode(gray)
        if result:
            return result, "raw"

        if not cfg.preprocessing_enabled:
            return [], "none"

        # Stage 1: locate a barcode-like region via gradient anisotropy.
        # A real attempt was already made (stage 0) and failed, so if no
        # such region exists anywhere in the crop, it very likely
        # contains no barcode at all — skip the expensive tail.
        region = None
        if cfg.presence_check_enabled:
            region = self._detect_barcode_region(gray)
            if region is None:
                return [], "no_barcode_region"

        working = gray

        # Stage 2: deskew — seed the angle from the detected region when
        # available (cheaper and more robust than a second Hough pass),
        # otherwise fall back to a dedicated Hough estimate.
        if cfg.deskew_enabled:
            angle = region["angle"] if region is not None else self._estimate_skew_angle(working)
            if abs(angle) > 0.5:
                working = self._rotate_image(working, angle)
                result = pyzbar.decode(working)
                if result:
                    return result, "deskew"

        # Stage 3: upscale — crop to the detected region first (+
        # padding) so upscaling isn't wasted on irrelevant background.
        if cfg.upscale_enabled:
            if region is not None:
                working = self._crop_to_region(working, region["bbox"])
            h, w = working.shape[:2]
            if min(h, w) < cfg.upscale_threshold:
                working = self._upscale(working)
                result = pyzbar.decode(working)
                if result:
                    return result, "upscale"

        # Stage 4: CLAHE contrast
        if cfg.clahe_enabled:
            working = self._apply_clahe(working)
            result = pyzbar.decode(working)
            if result:
                return result, "clahe"

        # Stage 5: adaptive threshold (not carried forward — denoise and
        # sharpen operate better on continuous grayscale than on a
        # binary mask)
        if cfg.adaptive_threshold_enabled:
            thresholded = self._adaptive_threshold(working)
            result = pyzbar.decode(thresholded)
            if result:
                return result, "adaptive_threshold"

        # Stage 6: denoise
        if cfg.denoise_enabled:
            working = self._denoise(working)
            result = pyzbar.decode(working)
            if result:
                return result, "denoise"

        # Stage 7: sharpen
        if cfg.sharpen_enabled:
            working = self._sharpen(working)
            result = pyzbar.decode(working)
            if result:
                return result, "sharpen"

        # Stage 8: fallback fixed-angle rotation — last resort for cases
        # the region/Hough estimate missed or misjudged.
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
    # PRESENCE PRE-CHECK
    # ------------------------------------------------------------------

    def _has_sufficient_edges(self, gray: np.ndarray) -> bool:
        """Cheapest possible gate: reject crops with almost no edges at all."""
        edges = cv2.Canny(
            gray,
            self._config.presence_edge_canny_threshold1,
            self._config.presence_edge_canny_threshold2,
        )
        density = float(np.count_nonzero(edges)) / max(edges.size, 1)
        return density >= self._config.presence_edge_density_min

    def _detect_barcode_region(self, gray: np.ndarray) -> dict | None:
        """Locate a barcode-like region via gradient anisotropy.

        Returns:
            None when no barcode-like region exists (strong signal
            there's no barcode in this crop). Otherwise a dict with
            "bbox" (x, y, w, h), "angle" (degrees, long-axis oriented),
            and "area_ratio".
        """
        cfg = self._config

        gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=cfg.presence_sobel_ksize)
        gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=cfg.presence_sobel_ksize)
        anisotropy = cv2.absdiff(cv2.convertScaleAbs(gx), cv2.convertScaleAbs(gy))

        blurred = cv2.blur(
            anisotropy, (cfg.presence_blur_kernel, cfg.presence_blur_kernel)
        )
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

        # Normalize angle toward the region's long axis so it represents
        # the barcode's true rotation rather than an arbitrary minAreaRect
        # convention.
        angle = rect_angle if rw < rh else rect_angle - 90.0

        return {
            "bbox": (x, y, w, h),
            "angle": float(angle),
            "area_ratio": float(area_ratio),
        }

    def _crop_to_region(
        self, gray: np.ndarray, bbox: tuple[int, int, int, int]
    ) -> np.ndarray:
        """Crop to the detected barcode region with padding, clamped to bounds."""
        x, y, w, h = bbox
        pad = int(self._config.presence_roi_padding)
        h_img, w_img = gray.shape[:2]

        x0 = max(0, x - pad)
        y0 = max(0, y - pad)
        x1 = min(w_img, x + w + pad)
        y1 = min(h_img, y + h + pad)

        cropped = gray[y0:y1, x0:x1]
        return cropped if cropped.size > 0 else gray

    # ------------------------------------------------------------------
    # STAGE 2: DESKEW
    # ------------------------------------------------------------------

    def _estimate_skew_angle(self, gray: np.ndarray) -> float:
        """Estimate skew angle from the dominant parallel-line direction.

        Fallback used only when region detection is disabled or didn't
        run; barcode bars are a strong parallel-line pattern, so Hough
        Line Transform reliably recovers arbitrary (non-90°-multiple)
        skew angles.
        """
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

        if not angles:
            return 0.0

        return float(np.median(angles))

    @staticmethod
    def _rotate_image(gray: np.ndarray, angle: float) -> np.ndarray:
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

    # ------------------------------------------------------------------
    # STAGE 3: UPSCALE
    # ------------------------------------------------------------------

    def _upscale(self, gray: np.ndarray) -> np.ndarray:
        cfg = self._config
        h, w = gray.shape[:2]
        scale = min(cfg.upscale_threshold / max(min(h, w), 1), cfg.upscale_max_factor)
        if scale <= 1.0:
            return gray
        interp = cv2.INTER_CUBIC if cfg.upscale_interpolation == "cubic" else cv2.INTER_LINEAR
        return cv2.resize(gray, None, fx=scale, fy=scale, interpolation=interp)

    # ------------------------------------------------------------------
    # STAGE 4: CONTRAST (CLAHE)
    # ------------------------------------------------------------------

    def _apply_clahe(self, gray: np.ndarray) -> np.ndarray:
        cfg = self._config
        clahe = cv2.createCLAHE(
            clipLimit=cfg.clahe_clip_limit,
            tileGridSize=(cfg.clahe_tile_grid_size, cfg.clahe_tile_grid_size),
        )
        return clahe.apply(gray)

    # ------------------------------------------------------------------
    # STAGE 5: ADAPTIVE THRESHOLD
    # ------------------------------------------------------------------

    def _adaptive_threshold(self, gray: np.ndarray) -> np.ndarray:
        cfg = self._config
        return cv2.adaptiveThreshold(
            gray,
            255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            cfg.adaptive_threshold_block_size,
            cfg.adaptive_threshold_c,
        )

    # ------------------------------------------------------------------
    # STAGE 6: DENOISE
    # ------------------------------------------------------------------

    def _denoise(self, gray: np.ndarray) -> np.ndarray:
        return cv2.fastNlMeansDenoising(gray, h=self._config.denoise_strength)

    # ------------------------------------------------------------------
    # STAGE 7: SHARPEN
    # ------------------------------------------------------------------

    def _sharpen(self, gray: np.ndarray) -> np.ndarray:
        cfg = self._config
        blurred = cv2.GaussianBlur(gray, (0, 0), cfg.sharpen_blur_sigma)
        return cv2.addWeighted(gray, cfg.sharpen_amount, blurred, 1.0 - cfg.sharpen_amount, 0)

    # ------------------------------------------------------------------
    # CONFIDENCE
    # ------------------------------------------------------------------

    def _compute_confidence(self, barcodes: list[dict]) -> float:
        """Combine decode quality, symbology trust, and cross-value ambiguity."""
        if not barcodes:
            return 0.0

        best = max(barcodes, key=lambda b: b["quality"])
        quality_signal = float(np.clip(best["quality"] / self._quality_saturation, 0.0, 1.0))
        type_trust = self._type_trust.get(best["type"], self._type_trust_default)

        distinct_values = {b["data"] for b in barcodes if b["data"]}
        ambiguity_penalty = 1.0 if len(distinct_values) <= 1 else self._ambiguity_penalty

        return float(np.clip(quality_signal * type_trust * ambiguity_penalty, 0.0, 1.0))