"""Dominant color extraction plugin.

Extracts dominant color evidence from a product crop.
"""

from __future__ import annotations

import cv2
import numpy as np

from src.core.config import AppConfig
from src.core.logger import get_logger
from src.core.utils import timer
from src.models.models import CropImage

logger = get_logger(__name__)


class ColorPlugin:
    """Extract dominant color evidence from a product crop."""

    name = "color"

    def __init__(self, config: AppConfig) -> None:
        """Initialize the color extraction plugin."""
        self._config = config.plugins.color

        logger.info(
            "ColorPlugin initialized "
            "(enabled=%s n_clusters=%d roi_enabled=%s "
            "roi_detection_size=%d clustering_size=%d "
            "kmeans_l_weight=%.2f)",
            self._config.enabled,
            self._config.n_clusters,
            self._config.roi_enabled,
            self._config.roi_detection_size,
            self._config.clustering_size,
            self._config.kmeans_l_weight,
        )

    def is_enabled(self) -> bool:
        """Return whether this plugin is enabled."""
        return self._config.enabled

    def process(self, crop: CropImage) -> dict:
        """Standard entrypoint wrapper for plugin execution."""
        if not self.is_enabled():
            logger.debug("ColorPlugin is disabled; skipping processing for crop_id='%s'", crop.crop_id)
            return {
                "dominant_color": None,
                "dominant_rgb": None,
                "representative_lab": None,
                "palette": [],
                "percentages": [],
                "roi": None,
                "debug": {"enabled": False},
            }
        return self.run(crop)

    # =========================================================================
    # Main pipeline
    # =========================================================================

    def run(self, crop: CropImage) -> dict:
        """Extract dominant color evidence from a product crop."""
        with timer() as elapsed:
            image = self._validate_image(crop.raw_image_array)

            # 1. Detect powder ROI
            roi_image, roi_info = self._extract_powder_roi(image)

            # 2. Resize ROI for K-Means
            clustering_size = max(16, int(self._config.clustering_size))
            small = self._resize_for_clustering(roi_image, clustering_size)

            # 2b. Center-based pixel weights
            small_h, small_w = small.shape[:2]

            if self._config.center_weight_enabled:
                yy, xx = np.mgrid[0:small_h, 0:small_w]
                center_y, center_x = small_h / 2.0, small_w / 2.0
                dist_norm = np.sqrt(
                    ((yy - center_y) / max(center_y, 1.0)) ** 2
                    + ((xx - center_x) / max(center_x, 1.0)) ** 2
                ).reshape(-1)

                sigma = max(float(self._config.center_weight_sigma_ratio), 1e-6)
                pixel_weights = np.exp(-(dist_norm ** 2) / (2.0 * sigma ** 2)).astype(np.float32)
            else:
                pixel_weights = np.ones(small_h * small_w, dtype=np.float32)

            # 3. Convert to Lab
            lab = cv2.cvtColor(small, cv2.COLOR_BGR2LAB)
            clustering_image = lab if self._config.use_lab else small

            # 4. Flatten pixels
            clustering_pixels = clustering_image.reshape(-1, 3)
            lab_pixels = lab.reshape(-1, 3)

            # 5. Remove extreme highlights + low-chroma glare
            if self._config.remove_highlights and self._config.use_lab:
                l_channel = lab_pixels[:, 0].astype(np.float32)
                a_channel = lab_pixels[:, 1].astype(np.float32) - 128.0
                b_channel = lab_pixels[:, 2].astype(np.float32) - 128.0
                chroma = np.sqrt(a_channel ** 2 + b_channel ** 2)

                too_bright = l_channel >= float(self._config.highlight_l_threshold)
                is_glare = (l_channel >= float(self._config.highlight_l_secondary_threshold)) & (
                    chroma <= float(self._config.highlight_chroma_threshold)
                )
                mask = ~(too_bright | is_glare)
            else:
                mask = np.ones(clustering_pixels.shape[0], dtype=bool)

            filtered_pixels = clustering_pixels[mask]
            filtered_weights = pixel_weights[mask]

            if filtered_pixels.shape[0] < max(10, int(self._config.n_clusters)):
                logger.warning(
                    "ColorPlugin highlight/glare filtering removed too many pixels for crop_id='%s'; using all ROI pixels",
                    crop.crop_id,
                )
                filtered_pixels = clustering_pixels
                mask = np.ones(clustering_pixels.shape[0], dtype=bool)
                filtered_weights = pixel_weights

            # 6. K-Means
            pixels = filtered_pixels.astype(np.float32)
            n_clusters = max(1, min(int(self._config.n_clusters), pixels.shape[0]))

            criteria = (
                cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER,
                int(self._config.kmeans_max_iter),
                float(self._config.kmeans_epsilon),
            )

            l_weight = float(self._config.kmeans_l_weight)
            kmeans_input = pixels.copy()

            if self._config.use_lab:
                kmeans_input[:, 0] = kmeans_input[:, 0] * l_weight

            _, labels, _ = cv2.kmeans(
                kmeans_input,
                n_clusters,
                None,
                criteria,
                int(self._config.kmeans_attempts),
                cv2.KMEANS_PP_CENTERS,
            )
            labels = labels.flatten()

            # Recompute centers from ORIGINAL (unweighted) pixels
            overall_mean = pixels.mean(axis=0)
            centers = np.zeros((n_clusters, 3), dtype=np.float32)

            for cluster_idx in range(n_clusters):
                cluster_mask = labels == cluster_idx
                if np.any(cluster_mask):
                    centers[cluster_idx] = pixels[cluster_mask].mean(axis=0)
                else:
                    centers[cluster_idx] = overall_mean

            # 7. Analyze clusters
            if self._config.center_weight_enabled:
                counts = np.bincount(labels, weights=filtered_weights, minlength=n_clusters)
            else:
                counts = np.bincount(labels, minlength=n_clusters).astype(np.float32)

            order = np.argsort(counts)[::-1]
            total_pixels = float(np.sum(counts))
            if total_pixels <= 0:
                total_pixels = float(pixels.shape[0])

            # Batch convert centers to Lab and BGR for high performance
            ordered_centers = centers[order]
            if self._config.use_lab:
                labs_batch = ordered_centers
                bgrs_batch = self._lab_to_bgr_batch(ordered_centers)
            else:
                labs_batch = self._bgr_to_lab_batch(ordered_centers)
                bgrs_batch = ordered_centers.astype(np.uint8)

            palette: list[str] = []
            percentages: list[float] = []
            lab_palette: list[list[float]] = []

            for i, cluster_idx in enumerate(order):
                lab_center = labs_batch[i]
                bgr_center = bgrs_batch[i]

                lab_palette.append([
                    round(float(lab_center[0]), 3),
                    round(float(lab_center[1]), 3),
                    round(float(lab_center[2]), 3),
                ])

                palette.append(self._bgr_to_hex(bgr_center))
                percentages.append(round(float(counts[cluster_idx]) / total_pixels, 4))

            # 8. Representative / dominant color selection
            min_chroma = float(self._config.min_dominant_chroma)
            chroma_qualified_order = []

            for i, cluster_idx in enumerate(order):
                candidate_lab = labs_batch[i]
                a_val = float(candidate_lab[1]) - 128.0
                b_val = float(candidate_lab[2]) - 128.0
                if np.sqrt(a_val ** 2 + b_val ** 2) >= min_chroma:
                    chroma_qualified_order.append(i)

            if chroma_qualified_order:
                best_pos = chroma_qualified_order[0]
                dominant_selection_method = "chroma_filtered"
            else:
                best_pos = 0
                dominant_selection_method = "fallback_highest_weight"

            representative_lab = labs_batch[best_pos]
            representative_bgr = bgrs_batch[best_pos]

            dominant_color = self._bgr_to_hex(representative_bgr)
            dominant_rgb = [
                int(representative_bgr[2]),
                int(representative_bgr[1]),
                int(representative_bgr[0]),
            ]

            # 9. Debug metadata
            pixels_removed = int(lab_pixels.shape[0] - filtered_pixels.shape[0])
            debug_info = {
                "pixels_used": int(pixels.shape[0]),
                "pixels_before_filter": int(lab_pixels.shape[0]),
                "highlight_pixels_removed": pixels_removed,
                "n_clusters": n_clusters,
                "clustering_size": clustering_size,
                "roi_detection_size": int(self._config.roi_detection_size),
                "kmeans_l_weight": l_weight,
                "color_space": "Lab" if self._config.use_lab else "BGR",
                "lab_palette": lab_palette,
                "dominant_selection_method": dominant_selection_method,
                "center_weighting_applied": bool(self._config.center_weight_enabled),
            }

        logger.info(
            "ColorPlugin extracted dominant color='%s' (method=%s l_weight=%.2f) "
            "for crop_id='%s' (roi_method=%s roi_score=%.3f roi_area=%.3f fallback=%s %.2f ms)",
            dominant_color,
            dominant_selection_method,
            l_weight,
            crop.crop_id,
            roi_info["method"],
            roi_info["score"],
            roi_info["area_ratio"],
            roi_info["fallback_used"],
            elapsed["elapsed_ms"],
        )

        return {
            "dominant_color": dominant_color,
            "dominant_rgb": dominant_rgb,
            "representative_lab": [
                round(float(representative_lab[0]), 3),
                round(float(representative_lab[1]), 3),
                round(float(representative_lab[2]), 3),
            ],
            "palette": palette,
            "percentages": percentages,
            "roi": roi_info,
            "debug": debug_info,
            "latency_ms": elapsed["elapsed_ms"],
        }

    # =========================================================================
    # ROI detection
    # =========================================================================

    def _extract_powder_roi(self, image: np.ndarray) -> tuple[np.ndarray, dict]:
        """Detect the large rectangular powder region."""
        height, width = image.shape[:2]

        if not self._config.roi_enabled:
            roi, bbox = self._center_roi(image)
            return roi, {
                "bbox": bbox,
                "area_ratio": (bbox[2] * bbox[3]) / float(width * height),
                "rectangularity": 1.0,
                "score": 0.0,
                "method": "center",
                "fallback_used": True,
            }

        detection_image = self._resize_for_detection(
            image, int(self._config.roi_detection_size)
        )
        det_h, det_w = detection_image.shape[:2]

        gray = cv2.cvtColor(detection_image, cv2.COLOR_BGR2GRAY)
        blur_kernel_size = max(3, int(self._config.blur_kernel_size))
        if blur_kernel_size % 2 == 0:
            blur_kernel_size += 1

        gray = cv2.GaussianBlur(gray, (blur_kernel_size, blur_kernel_size), 0)
        edges = cv2.Canny(
            gray,
            int(self._config.canny_threshold1),
            int(self._config.canny_threshold2),
        )

        if self._config.morphology_enabled:
            kernel_size = max(3, int(self._config.morphology_kernel_size))
            if kernel_size % 2 == 0:
                kernel_size += 1
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (kernel_size, kernel_size))
            edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel)

        contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        candidates: list[dict] = []
        image_area = float(det_w * det_h)
        center_x, center_y = det_w / 2.0, det_h / 2.0

        for contour in contours:
            contour_area = cv2.contourArea(contour)
            if contour_area <= 0:
                continue

            perimeter = cv2.arcLength(contour, True)
            if perimeter <= 0:
                continue

            epsilon = 0.03 * perimeter
            approx = cv2.approxPolyDP(contour, epsilon, True)
            if len(approx) < 4 or len(approx) > 8:
                continue

            x, y, w, h = cv2.boundingRect(approx)
            if w <= 0 or h <= 0:
                continue

            bbox_area = float(w * h)
            area_ratio = bbox_area / image_area

            if area_ratio < float(self._config.min_area_ratio) or area_ratio > float(self._config.max_area_ratio):
                continue

            rectangularity = contour_area / bbox_area if bbox_area > 0 else 0.0
            if rectangularity < float(self._config.min_rectangularity):
                continue

            aspect_ratio = w / float(h)
            if aspect_ratio < float(self._config.min_aspect_ratio) or aspect_ratio > float(self._config.max_aspect_ratio):
                continue

            candidate_center_x = x + w / 2.0
            candidate_center_y = y + h / 2.0

            dx = (candidate_center_x - center_x) / max(center_x, 1.0)
            dy = (candidate_center_y - center_y) / max(center_y, 1.0)
            distance = float(np.sqrt(dx * dx + dy * dy))
            center_score = max(0.0, 1.0 - distance)

            candidate_center_y_norm = candidate_center_y / max(float(det_h), 1.0)
            candidate_center_x_norm = candidate_center_x / max(float(det_w), 1.0)

            horizontal_score = 1.0 - abs(candidate_center_x_norm - 0.5) * 2.0
            target_y = float(self._config.lower_position_target_y)
            tolerance = max(float(self._config.lower_position_tolerance), 1e-6)
            vertical_distance = abs(candidate_center_y_norm - target_y)
            vertical_score = max(0.0, 1.0 - vertical_distance / tolerance)

            lower_position_score = float(np.clip(0.5 * horizontal_score + 0.5 * vertical_score, 0.0, 1.0))

            margin_ratio = min(x, y, det_w - (x + w), det_h - (y + h)) / max(float(min(det_w, det_h)), 1.0)
            inner_score = float(np.clip(margin_ratio / 0.15, 0.0, 1.0))

            area_range = max(float(self._config.max_area_ratio) - float(self._config.min_area_ratio), 1e-6)
            area_score = float(np.clip((area_ratio - float(self._config.min_area_ratio)) / area_range, 0.0, 1.0))

            score = (
                0.35 * rectangularity
                + 0.25 * center_score
                + 0.15 * lower_position_score
                + 0.15 * inner_score
                + 0.10 * area_score
            )

            candidates.append({
                "bbox": (x, y, w, h),
                "score": score,
                "area_ratio": area_ratio,
                "rectangularity": rectangularity,
            })

        if candidates:
            candidates.sort(key=lambda c: c["score"], reverse=True)
            best = candidates[0]
            x, y, w, h = best["bbox"]

            # Correct Bounding Box Scaling Fix
            scale_x = width / float(det_w)
            scale_y = height / float(det_h)

            x1 = max(0, min(width - 1, int(round(x * scale_x))))
            y1 = max(0, min(height - 1, int(round(y * scale_y))))
            x2 = max(x1 + 1, min(width, int(round((x + w) * scale_x))))
            y2 = max(y1 + 1, min(height, int(round((y + h) * scale_y))))

            orig_w = x2 - x1
            orig_h = y2 - y1

            roi = image[y1:y2, x1:x2]
            shrunken_roi = self._shrink_roi(roi)

            return shrunken_roi, {
                "bbox": [x1, y1, orig_w, orig_h],
                "area_ratio": best["area_ratio"],
                "rectangularity": best["rectangularity"],
                "score": round(float(best["score"]), 4),
                "method": "contour_rectangle",
                "fallback_used": False,
            }

        return self._lower_roi_fallback(image)

    # =========================================================================
    # Helpers
    # =========================================================================

    def _shrink_roi(self, roi: np.ndarray) -> np.ndarray:
        """Shrink ROI slightly towards center to eliminate potential package edges."""
        margin_ratio = float(self._config.roi_shrink_ratio)
        if margin_ratio <= 0.0:
            return roi

        h, w = roi.shape[:2]
        margin_x = int(round(w * margin_ratio))
        margin_y = int(round(h * margin_ratio))

        if w - 2 * margin_x < 8 or h - 2 * margin_y < 8:
            return roi

        return roi[margin_y: h - margin_y, margin_x: w - margin_x]

    def _center_roi(self, image: np.ndarray) -> tuple[np.ndarray, list[int]]:
        """Extract fall-back center ROI."""
        h, w = image.shape[:2]
        cx, cy = w // 2, h // 2
        rw, rh = int(w * 0.5), int(h * 0.5)

        x = max(0, cx - rw // 2)
        y = max(0, cy - rh // 2)
        w_box = min(w - x, rw)
        h_box = min(h - y, rh)

        return image[y: y + h_box, x: x + w_box], [x, y, w_box, h_box]

    def _lower_roi_fallback(self, image: np.ndarray) -> tuple[np.ndarray, dict]:
        """Extract lower-center fallback ROI when contour detection fails."""
        h, w = image.shape[:2]
        fw = int(w * float(self._config.fallback_lower_width_ratio))
        fh = int(h * float(self._config.fallback_lower_height_ratio))

        x = max(0, (w - fw) // 2)
        y = max(0, int(h * float(self._config.fallback_lower_y_ratio)))
        w_box = min(w - x, fw)
        h_box = min(h - y, fh)

        roi = image[y: y + h_box, x: x + w_box]
        bbox = [x, y, w_box, h_box]

        return roi, {
            "bbox": bbox,
            "area_ratio": (w_box * h_box) / float(w * h),
            "rectangularity": 1.0,
            "score": 0.0,
            "method": "lower_center_fallback",
            "fallback_used": True,
        }

    def _resize_for_detection(self, image: np.ndarray, target_size: int) -> np.ndarray:
        """Resize image preserving aspect ratio for ROI detection."""
        h, w = image.shape[:2]
        if max(h, w) <= target_size:
            return image

        scale = target_size / float(max(h, w))
        new_w, new_h = max(1, int(round(w * scale))), max(1, int(round(h * scale)))
        return cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_AREA)

    def _resize_for_clustering(self, image: np.ndarray, target_size: int) -> np.ndarray:
        """Resize ROI image for fast K-Means clustering."""
        h, w = image.shape[:2]
        if max(h, w) <= target_size:
            return image

        scale = target_size / float(max(h, w))
        new_w, new_h = max(1, int(round(w * scale))), max(1, int(round(h * scale)))
        return cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_AREA)

    @staticmethod
    def _bgr_to_lab_batch(bgr_array: np.ndarray) -> np.ndarray:
        """Convert array of BGR vectors (N, 3) to Lab space in a single batch call."""
        bgr_reshaped = bgr_array.reshape(-1, 1, 3).astype(np.uint8)
        lab_reshaped = cv2.cvtColor(bgr_reshaped, cv2.COLOR_BGR2LAB)
        return lab_reshaped.reshape(-1, 3).astype(np.float32)

    @staticmethod
    def _lab_to_bgr_batch(lab_array: np.ndarray) -> np.ndarray:
        """Convert array of Lab vectors (N, 3) to BGR space in a single batch call."""
        lab_reshaped = lab_array.reshape(-1, 1, 3).astype(np.uint8)
        bgr_reshaped = cv2.cvtColor(lab_reshaped, cv2.COLOR_LAB2BGR)
        return bgr_reshaped.reshape(-1, 3).astype(np.uint8)

    @staticmethod
    def _bgr_to_hex(bgr_pixel: np.ndarray) -> str:
        """Convert a BGR pixel array to HEX string."""
        b, g, r = int(bgr_pixel[0]), int(bgr_pixel[1]), int(bgr_pixel[2])
        return f"#{r:02x}{g:02x}{b:02x}"

    @staticmethod
    def _validate_image(image: np.ndarray) -> np.ndarray:
        """Ensure image array is non-empty and 3-channel BGR."""
        if image is None or image.size == 0:
            raise ValueError("Input image array is empty or None")

        if len(image.shape) == 2:
            return cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)

        if len(image.shape) == 3 and image.shape[2] == 4:
            return cv2.cvtColor(image, cv2.COLOR_BGRA2BGR)

        return image