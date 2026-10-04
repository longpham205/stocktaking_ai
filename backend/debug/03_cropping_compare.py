"""03_cropping_compare.py — Debug giai đoạn ④ CROPPING (engine/detection/cropper.py).

TRƯỚC ĐÂY (debug_dump_crops.py): chỉ có comment mô tả ý tưởng, KHÔNG có code
chạy thật. File này triển khai đúng ý tưởng đó:

    Đọc từng dòng của `records.csv` (sinh ra bởi `python -m engine --mode
    validate`, xem ValidationRunner._write_reports), dùng `image_key` +
    `detection_index` để dựng lại đúng crop đó (chạy lại Detector ->
    OverlapResolver -> Refiner -> Cropper trên đúng ảnh benchmark, KHÔNG
    chạy Retrieval/Decision/Plugin — vì mục tiêu chỉ là xem crop), rồi đặt
    cạnh 1 ảnh tham chiếu trong gallery của `gt_product_id` để so trực
    quan bằng mắt xem Cropper có cắt đúng/đủ vùng sản phẩm hay không.

Hiển thị 3 ô cho mỗi dòng:
    1. Ảnh gốc + bbox đã dùng để crop (từ CropImage.source_bbox)
    2. Crop ĐỘ PHÂN GIẢI THẤP (CropImage.image_array — cái Retriever thấy)
    3. Crop ĐỘ PHÂN GIẢI GỐC (CropImage.raw_image_array — cái Plugin thấy)
    + 1 ảnh tham chiếu gallery của gt_product_id (nếu tra được) làm nền so sánh.

Cách dùng:
    python -m engine --mode validate --benchmark-dir data/benchmark   # chạy 1 lần để có records.csv
    python debug/03_cropping_compare.py
    python debug/03_cropping_compare.py --only-mismatch
    python debug/03_cropping_compare.py --product-id 7

Controls: xem _shared/paged_viewer.py.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from matplotlib.patches import Rectangle

from debug._shared.bootstrap import gallery_folder_of, get_config, resolve_data_path
from debug._shared.formatting import parse_bool, safe_str
from debug._shared.io_utils import list_images, load_bgr, to_rgb
from debug._shared.paged_viewer import KeyboardPagedViewer


def _load_records(records_path: Path) -> list[dict]:
    with open(records_path, "r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _filter_records(rows: list[dict], product_id: str | None, only_mismatch: bool) -> list[dict]:
    out = []
    for row in rows:
        if not row.get("detection_index"):  # bỏ dòng "missed GT" (crop_id rỗng)
            continue
        if product_id is not None and row.get("gt_product_id") != product_id:
            continue
        if only_mismatch and parse_bool(row.get("correct"), default=False):
            continue
        out.append(row)
    return out


def _find_gallery_reference(cfg, gt_product_id: str | None) -> Path | None:
    if not gt_product_id:
        return None
    folder_name = gallery_folder_of(cfg, str(gt_product_id))
    if not folder_name:
        return None
    gallery_dir = resolve_data_path(cfg, "gallery_dir") / folder_name
    images = list_images(gallery_dir)
    return images[0] if images else None


class CroppingCompareViewer(KeyboardPagedViewer):
    def __init__(self, rows: list[dict], cfg, detector, overlap_resolver, refiner, cropper):
        super().__init__(item_count=len(rows), figsize=(15, 6), window_title="Cropping Compare Viewer")
        self.rows = rows
        self.cfg = cfg
        self.detector = detector
        self.overlap_resolver = overlap_resolver
        self.refiner = refiner
        self.cropper = cropper
        self._crops_cache: dict[str, tuple] = {}  # source_path -> (image_bgr, crops)

    def _crops_for_image(self, source_path: str):
        if source_path not in self._crops_cache:
            from engine.core.utils import generate_id
            from engine.models.models import ImageData

            image_bgr = load_bgr(source_path)
            image_data = ImageData(
                image_id=generate_id(), source_path=source_path, image_array=image_bgr,
                width=image_bgr.shape[1], height=image_bgr.shape[0],
            )
            detection_result = self.detector.detect(image_data)
            overlap_result = self.overlap_resolver.resolve(detection_result)
            refinement_result = self.refiner.refine(image_bgr, detection_result, overlap_result)
            crops = self.cropper.crop(image_data, detection_result, refinement_result)
            self._crops_cache[source_path] = (image_bgr, crops)
        return self._crops_cache[source_path]

    def render(self, fig, index: int) -> None:
        row = self.rows[index]
        source_path = row["source_path"]
        detection_index = int(row["detection_index"])
        gt_product_id = safe_str(row.get("gt_product_id"), default=None) if row.get("gt_product_id") else None

        image_bgr, crops = self._crops_for_image(source_path)
        crop = next((c for c in crops if c.detection_index == detection_index), None)

        ax1 = fig.add_subplot(1, 4, 1)
        ax1.imshow(to_rgb(image_bgr))
        ax1.axis("off")
        ax1.set_title("Ảnh gốc + bbox crop", fontsize=9)
        if crop is not None:
            b = crop.source_bbox
            ax1.add_patch(Rectangle((b.x1, b.y1), b.width, b.height, fill=False, edgecolor="#00C853", linewidth=2))

        ax2 = fig.add_subplot(1, 4, 2)
        ax2.axis("off")
        if crop is not None:
            ax2.imshow(to_rgb(crop.image_array))
        ax2.set_title(f"Crop chuẩn hoá\n(Retriever thấy)\n{self.cfg.cropping.target_size}", fontsize=9)

        ax3 = fig.add_subplot(1, 4, 3)
        ax3.axis("off")
        if crop is not None:
            ax3.imshow(to_rgb(crop.raw_image_array))
        ax3.set_title("Crop độ phân giải gốc\n(Plugin OCR/Color/Barcode thấy)", fontsize=9)

        ax4 = fig.add_subplot(1, 4, 4)
        ax4.axis("off")
        ref_path = _find_gallery_reference(self.cfg, gt_product_id)
        if ref_path is not None:
            ax4.imshow(to_rgb(load_bgr(ref_path)))
            ax4.set_title(f"Gallery tham chiếu\nGT product_id={gt_product_id}", fontsize=9)
        else:
            ax4.text(0.5, 0.5, "Không có ảnh\ngallery tham chiếu", ha="center", va="center")
            ax4.set_title(f"GT product_id={gt_product_id}", fontsize=9)

        status = "✓ ĐÚNG" if parse_bool(row.get("correct")) else "✗ SAI"
        fig.suptitle(
            f"[{index + 1}/{len(self.rows)}] {Path(source_path).name} | detection#{detection_index} | "
            f"used_refined_bbox={crop.used_refined_bbox if crop else 'N/A'} | "
            f"final={row.get('final_product_id')} vs gt={gt_product_id}  [{status}]",
            fontsize=11,
        )
        fig.tight_layout(rect=(0, 0, 1, 0.93))


def main() -> None:
    parser = argparse.ArgumentParser(description="So sánh trực quan crop (dual-resolution) với ảnh gallery tham chiếu.")
    parser.add_argument("--records", type=str, default=None, help="Đường dẫn records.csv (mặc định: data/outputs/records.csv).")
    parser.add_argument("--product-id", type=str, default=None, help="Chỉ xem các crop có gt_product_id này.")
    parser.add_argument("--only-mismatch", action="store_true", help="Chỉ xem các crop bị sai (correct=False).")
    parser.add_argument("--config", type=str, default=None)
    args = parser.parse_args()

    cfg = get_config(args.config)
    records_path = Path(args.records) if args.records else resolve_data_path(cfg, "output_dir") / "records.csv"

    if not records_path.is_file():
        print(f"[ERROR] Không tìm thấy {records_path}.")
        print("        Chạy 'python -m engine --mode validate --benchmark-dir data/benchmark' trước đã.")
        return

    rows = _load_records(records_path)
    rows = _filter_records(rows, args.product_id, args.only_mismatch)
    if not rows:
        print("[INFO] Không có dòng nào khớp bộ lọc.")
        return

    from engine.detection.cropper import Cropper
    from engine.detection.detector import Detector
    from engine.pipeline.overlap import OverlapResolver
    from engine.segmentation.refiner import Refiner

    detector = Detector(cfg)
    overlap_resolver = OverlapResolver(cfg)
    refiner = Refiner(cfg)
    cropper = Cropper(cfg)

    print(f"[INFO] {len(rows)} crop(s) khớp bộ lọc (từ {records_path}).")

    CroppingCompareViewer(rows, cfg, detector, overlap_resolver, refiner, cropper).show()


if __name__ == "__main__":
    main()
