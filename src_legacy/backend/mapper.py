"""Result Mapper: kết quả pipeline -> các dòng giỏ hàng. Luật gộp (ĐÃ CHỐT trong backend.md):

- item `accepted` cùng `product_id` -> gộp một dòng (quantity = số item);
- mỗi item `uncertain` -> GIỮ RIÊNG (không gộp, kể cả trùng SKU với dòng đã gộp);
- một SKU có thể có nhiều dòng; frontend không được giả định một SKU một dòng.
- `bboxes`: bbox của MỌI vật thuộc dòng (để vẽ ảnh kết quả); `bbox` = vật đầu tiên (cắt thumbnail).
"""

from __future__ import annotations

from pathlib import Path

from .inference import Detection


def merge_detections(dets: list[Detection]) -> list[dict]:
    lines: list[dict] = []
    accepted: dict[str, dict] = {}
    for d in dets:
        if d.status == "accepted":
            if d.product_id in accepted:
                accepted[d.product_id]["quantity"] += 1
                accepted[d.product_id]["bboxes"].append(d.bbox)
            else:
                line = {"product_id": d.product_id, "quantity": 1, "flagged": False,
                        "bbox": d.bbox, "bboxes": [d.bbox], "evidence": d.plugin_evidence}
                accepted[d.product_id] = line
                lines.append(line)
        elif d.status == "uncertain":
            lines.append({"product_id": d.product_id, "quantity": 1, "flagged": True,
                          "bbox": d.bbox, "bboxes": [d.bbox], "evidence": d.plugin_evidence})
    return lines


def save_thumbnail(image_bgr, bbox: tuple, out_path: Path, width: int = 240) -> bool:
    """Cắt vùng bbox từ ảnh gốc, thu nhỏ, lưu JPEG. Trả False nếu bbox không hợp lệ."""
    import cv2

    h, w = image_bgr.shape[:2]
    x1, y1 = max(0, int(bbox[0])), max(0, int(bbox[1]))
    x2, y2 = min(w, int(bbox[2])), min(h, int(bbox[3]))
    if x2 - x1 < 4 or y2 - y1 < 4:
        return False
    crop = image_bgr[y1:y2, x1:x2]
    scale = width / crop.shape[1]
    crop = cv2.resize(crop, (width, max(1, int(crop.shape[0] * scale))), interpolation=cv2.INTER_AREA)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    return bool(cv2.imwrite(str(out_path), crop, [cv2.IMWRITE_JPEG_QUALITY, 85]))
