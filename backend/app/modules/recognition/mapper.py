"""Recognition result -> order lines, and the image work around a capture (Pillow, no OpenCV: the
fake path must not load it).

The merge rule (the legacy web's, agreed with the cashiers):
- accepted objects of the same product become one line, quantity = how many;
- every uncertain object is a line of its own, even when its product already has a line;
- so a product may have several lines; the frontend must not assume one line per product.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps, UnidentifiedImageError

from app.modules.recognition.ports import Box, Detection

# an evidence blob longer than this is dropped rather than stored (a plugin dumping arrays)
MAX_EVIDENCE_CHARS = 8000


@dataclass
class Line:
    product_id: str
    quantity: int
    flagged: bool
    # every object of the line, for the boxes drawn on the photo; the first one is the thumbnail
    bboxes: list[Box] = field(default_factory=list)
    evidence: dict[str, Any] | None = None


def merge_detections(detections: list[Detection]) -> list[Line]:
    lines: list[Line] = []
    accepted: dict[str, Line] = {}
    for detection in detections:
        if detection.status == "accepted" and detection.product_id in accepted:
            line = accepted[detection.product_id]
            line.quantity += 1
            line.bboxes.append(detection.bbox)
        elif detection.status in ("accepted", "uncertain"):
            flagged = detection.status == "uncertain"
            line = Line(detection.product_id, 1, flagged, [detection.bbox], json_safe(detection.evidence))
            if not flagged:
                accepted[detection.product_id] = line
            lines.append(line)
    return lines


def json_safe(evidence: dict[str, Any] | None) -> dict[str, Any] | None:
    """Evidence as plain JSON: numpy numbers and arrays become numbers and lists, anything else
    unknown its text. None when empty or too long, so a capture never fails over its evidence."""
    if not evidence:
        return None

    def default(value: Any) -> Any:
        if hasattr(value, "tolist"):  # numpy scalars and arrays alike
            return value.tolist()
        return str(value)

    try:
        text = json.dumps(evidence, default=default, ensure_ascii=False)
    except (TypeError, ValueError):
        return None
    return json.loads(text) if len(text) <= MAX_EVIDENCE_CHARS else None


def is_image(data: bytes) -> bool:
    """The bytes decode as an image (the Content-Type header is not trusted)."""
    from io import BytesIO

    try:
        with Image.open(BytesIO(data)) as image:
            image.verify()
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, Image.DecompressionBombError):
        return False
    return True


def open_upright(path: Path) -> Image.Image | None:
    """The photo turned as its EXIF orientation says, as the engine sees it (OpenCV applies the
    orientation too), so boxes and thumbnails land on the right pixels. None if unreadable."""
    try:
        with Image.open(path) as image:
            return ImageOps.exif_transpose(image).convert("RGB")
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        return None


def image_size(path: Path) -> tuple[int, int] | None:
    image = open_upright(path)
    return image.size if image is not None else None


def save_thumbnail(image: Image.Image, bbox: Box, out_path: Path, width: int = 240) -> bool:
    """Crop the box, scale it to `width`, save a JPEG. False when the box is too small."""
    x1, y1 = max(0, int(bbox[0])), max(0, int(bbox[1]))
    x2, y2 = min(image.width, int(bbox[2])), min(image.height, int(bbox[3]))
    if x2 - x1 < 4 or y2 - y1 < 4:
        return False
    crop = image.crop((x1, y1, x2, y2))
    crop = crop.resize((width, max(1, round(crop.height * width / crop.width))), Image.Resampling.LANCZOS)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    crop.save(out_path, "JPEG", quality=85)
    return True
