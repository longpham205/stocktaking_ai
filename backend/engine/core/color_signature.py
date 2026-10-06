"""Colour signatures: which colours a product shows, learned from its gallery photos.

A signature is a 2-D histogram over the (a*, b*) plane of CIE Lab, counting only pixels that
HAVE a colour (white, grey, black and glare are ignored) and favouring the centre of the crop.
Lightness is left out on purpose: it follows the lighting of the photo, not the product
(measured 2026-10-06: adding lightness bins made the comparison worse). Exposure still moves
a colour towards or away from the neutral centre, which is one more reason to keep every
gallery photo as its own exemplar.

Each SKU keeps one signature per gallery photo instead of one average. A product that differs
from its sibling on a single face then matches strongly when that face is visible and not at
all otherwise; an average would dilute the distinguishing face.

No per-product region of interest and no hand-typed reference colour is involved.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

BINS = 16
AB_RANGE = 80.0  # histogram covers a*, b* in [-80, 80]; more saturated pixels are not counted
RESIZE_SIDE = 96
CHROMA_FLOOR = 8.0  # below this a pixel is neutral and does not count
CHROMA_FULL = 20.0  # at or above this a pixel counts fully
GLARE_LIGHTNESS = 245  # OpenCV 8-bit L
CENTER_SIGMA = 0.7  # width of the centre weighting, relative to half the crop size


def compute_signature(image_bgr: np.ndarray) -> tuple[np.ndarray, float]:
    """Compute the colour signature of a product crop.

    Args:
        image_bgr: Crop in BGR, any size.

    Returns:
        (histogram, colored_fraction). The histogram is BINS x BINS float32 and sums to 1, or is
        all zeros when the crop has no coloured pixel. `colored_fraction` is the centre-weighted
        share of pixels that have a colour, in [0, 1]; a white box scores about 0.01.
    """
    scale = RESIZE_SIDE / max(image_bgr.shape[:2])
    if scale < 1:
        image_bgr = cv2.resize(image_bgr, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)

    lab = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
    lightness, a, b = lab[..., 0], lab[..., 1] - 128.0, lab[..., 2] - 128.0
    weight = np.clip((np.hypot(a, b) - CHROMA_FLOOR) / (CHROMA_FULL - CHROMA_FLOOR), 0.0, 1.0)
    weight[lightness >= GLARE_LIGHTNESS] = 0.0

    height, width = image_bgr.shape[:2]
    yy, xx = np.mgrid[0:height, 0:width]
    distance_sq = ((yy - height / 2) / (height / 2)) ** 2 + ((xx - width / 2) / (width / 2)) ** 2
    weight = weight * np.exp(-distance_sq / (2 * CENTER_SIGMA**2))

    histogram, _, _ = np.histogram2d(
        a.ravel(), b.ravel(), bins=BINS, range=((-AB_RANGE, AB_RANGE), (-AB_RANGE, AB_RANGE)), weights=weight.ravel()
    )
    histogram = cv2.GaussianBlur(histogram.astype(np.float32), (3, 3), 0)
    total = float(histogram.sum())
    if total > 0:
        histogram = histogram / total
    return histogram, float(weight.mean())


def signature_similarity(query: np.ndarray, exemplars: np.ndarray) -> float:
    """Best histogram intersection between a query signature and a SKU's exemplars, in [0, 1]."""
    if len(exemplars) == 0:
        return 0.0
    return float(np.minimum(exemplars, query[None, ...]).sum(axis=(1, 2)).max())


class ColorSignatureStore:
    """Per-SKU colour signatures built from the gallery (scripts/build_color_signatures.py)."""

    def __init__(self, exemplars: dict[str, np.ndarray]) -> None:
        self._exemplars = exemplars

    @classmethod
    def load(cls, path: Path) -> "ColorSignatureStore":
        """Load the signature file. A missing file is an error: colour evidence was asked for."""
        if not path.is_file():
            raise FileNotFoundError(
                f"Colour signature file not found: {path}. Build it with "
                "'python scripts/build_color_signatures.py' or set plugins.color.mode back to 'roi'."
            )
        with np.load(path) as data:
            return cls({key: data[key].astype(np.float32) for key in data.files})

    def save(self, path: Path) -> None:
        """Write the signatures as one array of exemplars per product id."""
        path.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(path, **self._exemplars)

    def product_ids(self) -> set[str]:
        """Product ids that have at least one exemplar."""
        return set(self._exemplars)

    def similarity(self, query: np.ndarray, product_id: str) -> float | None:
        """Similarity of a query signature to one SKU, or None when the SKU has no exemplar."""
        exemplars = self._exemplars.get(str(product_id))
        if exemplars is None or len(exemplars) == 0:
            return None
        return signature_similarity(query, exemplars)
