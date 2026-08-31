"""General-purpose utility functions shared across modules.

This module intentionally contains no AI/ML inference logic and no UI
rendering logic (see 02_MODULE_SPECIFICATION.md, Section 1).
"""

from __future__ import annotations

import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import cv2
import numpy as np

from src.core.logger import get_logger

logger = get_logger(__name__)

IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".bmp", ".webp")


def ensure_dir(path: str | Path) -> Path:
    """Ensures a directory exists, creating it (and parents) if necessary.

    Args:
        path: Directory path to ensure.

    Returns:
        The resolved Path object of the directory.
    """
    resolved = Path(path)
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def generate_id(prefix: str = "") -> str:
    """Generates a short unique identifier.

    Args:
        prefix: Optional prefix to prepend to the generated identifier.

    Returns:
        A unique string identifier.
    """
    unique_part = uuid.uuid4().hex[:12]
    return f"{prefix}{unique_part}" if prefix else unique_part


def list_image_files(directory: str | Path) -> list[Path]:
    """Lists all supported image files within a directory (non-recursive by default).

    Args:
        directory: Directory to scan for image files.

    Returns:
        Sorted list of image file paths.
    """
    dir_path = Path(directory)
    if not dir_path.is_dir():
        return []
    files = [
        candidate
        for candidate in dir_path.iterdir()
        if candidate.is_file() and candidate.suffix.lower() in IMAGE_EXTENSIONS
    ]
    return sorted(files)


def load_image_bgr(image_path: str | Path) -> np.ndarray:
    """Loads an image from disk into a BGR NumPy array.

    Args:
        image_path: Path to the image file.

    Returns:
        Image array in BGR channel order with shape (H, W, 3).

    Raises:
        ValueError: If the image could not be decoded.
    """
    path = Path(image_path)

    try:
        image_data = np.fromfile(path, dtype=np.uint8)
        image = cv2.imdecode(image_data, cv2.IMREAD_COLOR)
    except Exception as exc:
        raise ValueError(f"Failed to read image at path: {path}") from exc

    if image is None:
        raise ValueError(f"Failed to decode image at path: {path}")

    return image


def save_image_bgr(image: np.ndarray, output_path: str | Path) -> str:
    """Saves a BGR NumPy array to disk as an image file.

    Args:
        image: Image array in BGR channel order.
        output_path: Destination file path.

    Returns:
        The string path of the saved file.

    Raises:
        ValueError: If the image could not be written.
    """
    output = Path(output_path)
    ensure_dir(output.parent)
    success = cv2.imwrite(str(output), image)
    if not success:
        raise ValueError(f"Failed to write image to path: {output}")
    return str(output)


@contextmanager
def timer() -> Iterator[dict[str, float]]:
    """Context manager measuring elapsed wall-clock time in milliseconds.

    Yields:
        A dictionary that will contain the key 'elapsed_ms' populated once
        the `with` block exits.

    Example:
        with timer() as t:
            do_work()
        print(t["elapsed_ms"])
    """
    result: dict[str, float] = {"elapsed_ms": 0.0}
    start = time.perf_counter()
    try:
        yield result
    finally:
        result["elapsed_ms"] = (time.perf_counter() - start) * 1000.0


def clip_coordinate(value: float, minimum: float, maximum: float) -> float:
    """Clamps a numeric value into the inclusive [minimum, maximum] range.

    Args:
        value: Value to clamp.
        minimum: Lower bound.
        maximum: Upper bound.

    Returns:
        The clamped value.
    """
    return max(minimum, min(value, maximum))
