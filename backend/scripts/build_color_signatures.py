"""Lập chữ ký màu của từng SKU từ ảnh gallery (dùng cho `plugins.color.mode: "signature"`).

    python scripts/build_color_signatures.py                  # từ backend/, cần GPU trống (~2 phút)
    python scripts/build_color_signatures.py --config configs/config.demo.yaml

Ảnh gallery chụp cả nền, nên mỗi ảnh được cắt theo khung detect tin cậy nhất rồi mới tính chữ ký
(`engine/core/color_signature.py`). Ảnh không detect được vật nào bị bỏ qua và in ra để biết.
Kết quả ghi vào `plugins.color.signatures_path` (mặc định `data/cache/color_signatures.npz`).

Chạy lại mỗi khi thêm/đổi ảnh gallery hoặc thêm SKU: SKU chưa có chữ ký thì bước fusion không dùng
bằng chứng màu cho mọi crop có SKU đó trong Top-K.
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Lập chữ ký màu của từng SKU từ ảnh gallery.")
    parser.add_argument("--config", type=Path, help="file config của pipeline (mặc định configs/config.yaml)")
    args = parser.parse_args(argv)

    from engine.catalog.factory import open_catalog_repository
    from engine.core.color_signature import ColorSignatureStore, compute_signature
    from engine.core.config import build_config, load_config
    from engine.core.utils import generate_id, list_image_files, load_image_bgr
    from engine.detection.detector import Detector
    from engine.models.models import ImageData

    config = build_config(args.config) if args.config else load_config()
    gallery_dir = config.resolve_path(config.paths.gallery_dir)
    target = config.resolve_path(config.plugins.color.signatures_path)
    folders = open_catalog_repository(config).folder_to_product_id()
    detector = Detector(config)

    signatures: dict[str, list[np.ndarray]] = defaultdict(list)
    skipped: list[str] = []
    for folder in sorted(path for path in gallery_dir.iterdir() if path.is_dir()):
        product_id = folders.get(folder.name)
        if product_id is None:
            print(f"Bỏ qua thư mục không có trong catalog: {folder.name}")
            continue
        for photo in list_image_files(folder):
            array = load_image_bgr(photo)
            height, width = array.shape[:2]
            image = ImageData(image_id=generate_id(prefix="gal_"), source_path=str(photo), image_array=array, width=width, height=height)
            detections = detector.detect(image).detections
            if not detections:
                skipped.append(f"{folder.name}/{Path(photo).name}")
                continue
            box = detections[0].bbox
            x1, y1, x2, y2 = (int(max(0, value)) for value in (box.x1, box.y1, box.x2, box.y2))
            histogram, _ = compute_signature(array[y1:y2, x1:x2])
            if float(histogram.sum()) > 0:
                signatures[str(product_id)].append(histogram)

    without = sorted(set(map(str, folders.values())) - set(signatures), key=lambda pid: (len(pid), pid))
    ColorSignatureStore({pid: np.stack(items) for pid, items in signatures.items()}).save(target)
    print(f"Đã ghi {target}: {len(signatures)} SKU, {sum(len(v) for v in signatures.values())} chữ ký")
    if skipped:
        print(f"Ảnh không detect được vật nào ({len(skipped)}): " + ", ".join(skipped))
    if without:
        print(f"SKU CHƯA có chữ ký màu: {', '.join(without)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
