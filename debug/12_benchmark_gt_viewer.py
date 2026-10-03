"""12_benchmark_gt_viewer.py — Xem nhanh ground truth benchmark (COCO), CHƯA
chạy pipeline gì cả (chỉ đọc _annotations.coco.json + crop theo bbox GT).
Dùng để kiểm tra nhãn benchmark có đúng/sạch trước khi tốn thời gian chạy
`--mode validate` (thay thế cả debug_benchmark.py cũ, vốn chỉ xem được 1
ảnh và không tương tác).

ĐÃ SỬA LỖI so với debug_benchmark_labels.py gốc:
    - ``ANNOTATION_FILE`` trỏ vào ``data/benchmark/products.json`` — SAI
      file (đó là catalog SKU, không phải COCO annotation; COCO thật nằm
      ở ``data/benchmark/_annotations.coco.json`` = ``config.paths.
      benchmark_labels_dir``). Với path sai này, ``load_coco()`` sẽ ném
      lỗi hoặc đọc nhầm cấu trúc JSON ngay từ bước đầu.
    - Dùng ``cv2.imread`` (không đọc được path Unicode/tên tiếng Nhật) ->
      đổi sang ``load_bgr`` (an toàn Unicode, dùng chung mọi nơi trong debug/).

Cách dùng:
    python debug/12_benchmark_gt_viewer.py --labels 5
    python debug/12_benchmark_gt_viewer.py --labels 5,6,7 --max-per-label 20

Controls: xem _shared/paged_viewer.py.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from matplotlib.patches import Rectangle

from debug._shared.bootstrap import get_config, resolve_data_path
from debug._shared.io_utils import load_bgr, to_rgb
from debug._shared.paged_viewer import KeyboardPagedViewer


def _load_coco(annotation_file: Path):
    with open(annotation_file, "r", encoding="utf-8") as f:
        coco = json.load(f)
    images = {img["id"]: img for img in coco.get("images", [])}
    categories = {cat["id"]: cat for cat in coco.get("categories", [])}
    return images, categories, coco.get("annotations", [])


def _find_image(images_dir: Path, file_name: str) -> Path | None:
    direct = images_dir / file_name
    if direct.is_file():
        return direct
    matches = list(images_dir.rglob(Path(file_name).name))
    return matches[0] if matches else None


def _crop_bbox(image, bbox, padding: int = 0):
    h, w = image.shape[:2]
    x, y, bw, bh = bbox
    x1, y1 = max(0, int(x - padding)), max(0, int(y - padding))
    x2, y2 = min(w, int(x + bw + padding)), min(h, int(y + bh + padding))
    if x2 <= x1 or y2 <= y1:
        return None
    return image[y1:y2, x1:x2]


def _build_samples(images_dir, images, categories, annotations, target_labels, max_per_label) -> list[dict]:
    samples, counter = [], {}
    for ann in annotations:
        category_id = ann["category_id"]
        if target_labels and category_id not in target_labels:
            continue
        image_info = images.get(ann["image_id"])
        if image_info is None:
            continue
        image_path = _find_image(images_dir, image_info["file_name"])
        if image_path is None:
            print(f"[WARNING] Không tìm thấy ảnh: {image_info['file_name']}")
            continue
        if max_per_label is not None and counter.get(category_id, 0) >= max_per_label:
            continue
        counter[category_id] = counter.get(category_id, 0) + 1
        samples.append({
            "image_path": image_path, "image_id": ann["image_id"], "annotation_id": ann["id"],
            "category_id": category_id, "category_name": categories.get(category_id, {}).get("name", f"category_{category_id}"),
            "bbox": ann["bbox"],
        })
    return samples


class BenchmarkGtViewer(KeyboardPagedViewer):
    def __init__(self, samples: list[dict]):
        super().__init__(item_count=len(samples), figsize=(14, 7), window_title="Benchmark GT Viewer")
        self.samples = samples

    def render(self, fig, index: int) -> None:
        sample = self.samples[index]
        image = load_bgr(sample["image_path"])
        crop = _crop_bbox(image, sample["bbox"])

        ax1 = fig.add_subplot(1, 2, 1)
        ax1.imshow(to_rgb(image))
        ax1.axis("off")
        ax1.set_title("Benchmark Image", fontsize=13)
        x, y, w, h = sample["bbox"]
        ax1.add_patch(Rectangle((x, y), w, h, fill=False, linewidth=2, edgecolor="lime"))
        ax1.text(x, max(0, y - 5), f"ID {sample['category_id']} - {sample['category_name']}", fontsize=10, backgroundcolor="white")

        ax2 = fig.add_subplot(1, 2, 2)
        ax2.axis("off")
        ax2.set_title("Product Crop (theo GT bbox)", fontsize=13)
        if crop is None:
            ax2.text(0.5, 0.5, "Crop không hợp lệ", ha="center", va="center")
        else:
            ax2.imshow(to_rgb(crop))

        fig.suptitle(
            f"[{index + 1}/{len(self.samples)}]  Label: {sample['category_id']} ({sample['category_name']})\n"
            f"Ảnh: {sample['image_path'].name}   |   image_id: {sample['image_id']}   |   "
            f"annotation_id: {sample['annotation_id']}   |   bbox: {sample['bbox']}",
            fontsize=11,
        )
        fig.tight_layout()


def main() -> None:
    parser = argparse.ArgumentParser(description="Xem nhanh ground truth benchmark COCO (chưa chạy pipeline).")
    parser.add_argument("--labels", type=str, default="", help='category_id cần xem, cách nhau dấu phẩy, VD "5,6". Rỗng = xem tất cả.')
    parser.add_argument("--max-per-label", type=int, default=None)
    parser.add_argument("--config", type=str, default=None)
    args = parser.parse_args()

    cfg = get_config(args.config)
    images_dir = resolve_data_path(cfg, "benchmark_images_dir")
    annotation_file = resolve_data_path(cfg, "benchmark_labels_dir")
    target_labels = {int(x) for x in args.labels.split(",") if x.strip()} or None

    print("=" * 70)
    print("BENCHMARK GT VIEWER")
    print(f"Images     : {images_dir}")
    print(f"Annotation : {annotation_file}")
    print(f"Labels     : {sorted(target_labels) if target_labels else 'ALL'}")
    print("=" * 70)

    if not images_dir.is_dir():
        print(f"[ERROR] Không tồn tại: {images_dir}")
        return
    if not annotation_file.is_file():
        print(f"[ERROR] Không tồn tại: {annotation_file}")
        return

    images, categories, annotations = _load_coco(annotation_file)
    print(f"Images: {len(images)}  Categories: {len(categories)}  Annotations: {len(annotations)}")
    print("\nAvailable categories:")
    for category_id, category in categories.items():
        print(f"  {category_id}: {category.get('name', '')}")

    samples = _build_samples(images_dir, images, categories, annotations, target_labels, args.max_per_label)
    print(f"\nTìm thấy {len(samples)} annotation khớp bộ lọc.")

    counter: dict[int, int] = {}
    for sample in samples:
        counter[sample["category_id"]] = counter.get(sample["category_id"], 0) + 1
    for category_id, count in counter.items():
        name = categories.get(category_id, {}).get("name", f"category_{category_id}")
        print(f"  {category_id} - {name}: {count} crop")

    BenchmarkGtViewer(samples).show()


if __name__ == "__main__":
    main()
