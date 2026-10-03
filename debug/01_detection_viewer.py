"""01_detection_viewer.py — Debug giai đoạn ① DETECTION (src/detection/detector.py).

TRƯỚC ĐÂY: không có file debug nào riêng cho stage Detection — đây là file
MỚI, lấp khoảng trống đó.

Hiển thị trực tiếp output của ``Detector.detect()`` (class-agnostic, không
qua Retrieval/Decision/Plugin) trên một ảnh hoặc cả thư mục ảnh:
    - Mọi bounding box detector tìm được + confidence.
    - Nếu có file COCO annotation (``--gt``), vẽ chồng thêm GT để so trực
      quan detector "bắt đủ" hay "bắt thiếu/thừa" bao nhiêu box.

Cách dùng:
    python debug/01_detection_viewer.py
    python debug/01_detection_viewer.py --source data/benchmark/images --gt data/benchmark/_annotations.coco.json
    python debug/01_detection_viewer.py --source path/to/one_image.jpg

Controls: xem _shared/paged_viewer.py (←/→, Home/End, S để lưu, Esc thoát).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

from debug._shared.bootstrap import get_config, resolve_data_path
from debug._shared.formatting import format_number
from debug._shared.io_utils import list_images, load_bgr, to_rgb
from debug._shared.paged_viewer import KeyboardPagedViewer

_COLOR_DETECTION = "#00C853"  # xanh lá — box của Detector
_COLOR_GT = "#FFD600"  # vàng, nét đứt — ground truth


def _load_coco_gt(gt_path: Path) -> dict[str, list[list[float]]]:
    """Trả về {file_name: [[x, y, w, h], ...]} từ 1 file COCO annotation."""
    with open(gt_path, "r", encoding="utf-8") as f:
        coco = json.load(f)

    images_by_id = {img["id"]: img["file_name"] for img in coco.get("images", [])}
    boxes_by_file: dict[str, list[list[float]]] = {}
    for ann in coco.get("annotations", []):
        file_name = images_by_id.get(ann["image_id"])
        if file_name is None:
            continue
        boxes_by_file.setdefault(file_name, []).append(ann["bbox"])
    return boxes_by_file


class DetectionViewer(KeyboardPagedViewer):
    """Chạy Detector trên từng ảnh khi cần tới (lazy), có cache kết quả."""

    def __init__(self, image_paths: list[Path], detector, gt_by_file: dict[str, list[list[float]]] | None):
        super().__init__(item_count=len(image_paths), figsize=(11, 9), window_title="Detection Viewer")
        self.image_paths = image_paths
        self.detector = detector
        self.gt_by_file = gt_by_file or {}
        self._cache: dict[int, object] = {}

    def _detect(self, index: int):
        if index not in self._cache:
            from src.core.utils import generate_id
            from src.models.models import ImageData

            image_bgr = load_bgr(self.image_paths[index])
            image_data = ImageData(
                image_id=generate_id(),
                source_path=str(self.image_paths[index]),
                image_array=image_bgr,
                width=image_bgr.shape[1],
                height=image_bgr.shape[0],
            )
            self._cache[index] = (image_bgr, self.detector.detect(image_data))
        return self._cache[index]

    def render(self, fig, index: int) -> None:
        path = self.image_paths[index]
        image_bgr, detection_result = self._detect(index)

        ax = fig.add_subplot(1, 1, 1)
        ax.imshow(to_rgb(image_bgr))
        ax.axis("off")

        for det_index, detection in enumerate(detection_result.detections):
            box = detection.bbox
            ax.add_patch(
                Rectangle(
                    (box.x1, box.y1),
                    box.width,
                    box.height,
                    fill=False,
                    edgecolor=_COLOR_DETECTION,
                    linewidth=2,
                )
            )
            ax.text(
                box.x1,
                max(box.y1 - 6, 10),
                f"#{det_index} conf={format_number(detection.confidence, 2)}",
                color="black",
                fontsize=9,
                bbox=dict(facecolor=_COLOR_DETECTION, alpha=0.85, pad=2),
            )

        gt_boxes = self.gt_by_file.get(path.name, [])
        for x, y, w, h in gt_boxes:
            ax.add_patch(
                Rectangle((x, y), w, h, fill=False, edgecolor=_COLOR_GT, linewidth=2, linestyle="--")
            )

        n_det = len(detection_result.detections)
        n_gt = len(gt_boxes)
        title = (
            f"[{index + 1}/{len(self.image_paths)}] {path.name}\n"
            f"Detections: {n_det}"
            + (f"   |   GT boxes: {n_gt}" if self.gt_by_file else "")
            + f"   |   detector latency: {format_number(detection_result.processing_time_ms, 1)} ms"
        )
        ax.set_title(title, fontsize=11)
        fig.tight_layout()


def main() -> None:
    parser = argparse.ArgumentParser(description="Debug giai đoạn Detection (class-agnostic).")
    parser.add_argument("--source", type=str, default=None, help="1 ảnh hoặc 1 thư mục ảnh (mặc định: data/benchmark/images nếu có, ngược lại data/query).")
    parser.add_argument("--gt", type=str, default=None, help="File COCO _annotations.coco.json để vẽ chồng ground truth (tuỳ chọn).")
    parser.add_argument("--config", type=str, default=None, help="Đường dẫn configs/config.yaml khác (tuỳ chọn).")
    args = parser.parse_args()

    cfg = get_config(args.config)

    if args.source:
        source = Path(args.source)
    else:
        benchmark_images = resolve_data_path(cfg, "benchmark_images_dir")
        source = benchmark_images if benchmark_images.is_dir() else resolve_data_path(cfg, "query_dir")

    if source.is_file():
        image_paths = [source]
    else:
        image_paths = list_images(source)

    if not image_paths:
        print(f"[ERROR] Không tìm thấy ảnh nào trong: {source}")
        return

    gt_by_file = None
    default_gt_path = resolve_data_path(cfg, "benchmark_labels_dir")
    gt_path = Path(args.gt) if args.gt else (default_gt_path if source == resolve_data_path(cfg, "benchmark_images_dir") else None)
    if gt_path and gt_path.is_file():
        gt_by_file = _load_coco_gt(gt_path)
        print(f"[INFO] Đã nạp ground truth từ: {gt_path}")

    from src.detection.detector import Detector

    detector = Detector(cfg)

    print(f"[INFO] Nguồn ảnh: {source}  ({len(image_paths)} ảnh)")
    print(f"[INFO] Backend detection: {cfg.detection.backend}")

    DetectionViewer(image_paths, detector, gt_by_file).show()


if __name__ == "__main__":
    main()
