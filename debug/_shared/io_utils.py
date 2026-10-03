"""Tiện ích đọc/ghi ảnh dùng chung cho debug/.

``cv2.imread``/``cv2.imwrite`` KHÔNG đọc được path chứa ký tự Unicode
(tên thư mục gallery tiếng Nhật như "外箱ABA", "マジョリカマジョルカ...")
trên Windows — trả về ``None`` âm thầm thay vì lỗi rõ ràng. Trước đây chỉ
``check_barcode.py`` xử lý đúng việc này qua ``np.fromfile`` + ``cv2.imdecode``;
các file debug khác dùng ``cv2.imread``/``load_image_bgr`` thường nên sẽ
lỗi khi trỏ vào gallery thật. Toàn bộ debug/ nay dùng ``load_bgr`` /
``save_bgr`` ở đây để tránh lặp lại lỗi đó.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np


def load_bgr(path: str | Path) -> np.ndarray:
    """Đọc ảnh BGR an toàn với path Unicode (tên tiếng Nhật, dấu tiếng Việt...).

    Raises:
        FileNotFoundError: nếu file không tồn tại.
        ValueError: nếu file tồn tại nhưng không decode được thành ảnh.
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"Không tìm thấy ảnh: {path}")

    raw_bytes = np.fromfile(str(path), dtype=np.uint8)
    image = cv2.imdecode(raw_bytes, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"Không decode được ảnh (file hỏng hoặc không phải ảnh): {path}")
    return image


def save_bgr(path: str | Path, image: np.ndarray) -> None:
    """Ghi ảnh BGR an toàn với path Unicode. Tự tạo thư mục cha nếu chưa có."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ext = path.suffix or ".jpg"
    ok, buffer = cv2.imencode(ext, image)
    if not ok:
        raise ValueError(f"Không encode được ảnh khi lưu: {path}")
    buffer.tofile(str(path))


def to_rgb(image_bgr: np.ndarray) -> np.ndarray:
    """Chuyển BGR (OpenCV) -> RGB (matplotlib)."""
    return cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)


def list_images(directory: str | Path, extensions: tuple[str, ...] = (".jpg", ".jpeg", ".png", ".bmp", ".webp")) -> list[Path]:
    """Liệt kê ảnh trong 1 thư mục (không đệ quy), sắp xếp theo tên."""
    directory = Path(directory)
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.iterdir() if p.is_file() and p.suffix.lower() in extensions)


def crop_to_object(image_bgr: np.ndarray, pad_ratio: float = 0.06, min_area_ratio: float = 0.01) -> np.ndarray:
    """Cắt ảnh sát vật chính để XEM (chỉ dùng cho viewer, không dùng trong pipeline).

    Tách theo cạnh (Canny) để không bị nhiễu bởi nền có dải sáng / vật cùng màu nền; lấy vùng cạnh
    lớn nhất không chạm viền ảnh, cắt kèm lề. Không chắc chắn -> trả nguyên ảnh.
    """
    h, w = image_bgr.shape[:2]
    scale = 512.0 / max(h, w) if max(h, w) > 512 else 1.0
    small = cv2.resize(image_bgr, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA) if scale < 1 else image_bgr
    sh, sw = small.shape[:2]
    gray = cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), (5, 5), 0)
    edges = cv2.Canny(gray, 30, 90)
    edges = cv2.dilate(edges, np.ones((5, 5), np.uint8), iterations=2)
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    margin = 3
    inner, touching = [], []
    for c in contours:
        x, y, bw, bh = cv2.boundingRect(c)
        touches = x <= margin or y <= margin or x + bw >= sw - margin or y + bh >= sh - margin
        (touching if touches else inner).append((bw * bh, x, y, bw, bh))
    inner = [b for b in inner if b[0] >= min_area_ratio * sw * sh]
    if not inner:
        return image_bgr
    # Vùng cạnh lớn nhất chạm mép (vật tràn khung / nền vân gỗ) -> không chắc vật nào là chính: giữ nguyên.
    if touching and max(touching)[0] > max(inner)[0]:
        return image_bgr
    _, x, y, bw, bh = max(inner)
    if bw * bh > 0.9 * sw * sh:
        return image_bgr
    pad = int(pad_ratio * max(bw, bh))
    x0, y0 = max(0, x - pad), max(0, y - pad)
    x1, y1 = min(sw, x + bw + pad), min(sh, y + bh + pad)
    return image_bgr[int(y0 / scale):int(y1 / scale), int(x0 / scale):int(x1 / scale)]
