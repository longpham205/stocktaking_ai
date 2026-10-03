"""02_overlap_segmentation_viewer.py — Debug giai đoạn ② OVERLAP
(src/pipeline/overlap.py) và ③ SEGMENTATION (src/segmentation/refiner.py).

TRƯỚC ĐÂY: không có file debug nào cho 2 stage này — đây là file MỚI.

Chạy đúng 3 bước đầu của pipeline (Detector -> OverlapResolver -> Refiner),
KHÔNG chạy Retrieval/Decision/Plugin, để cô lập câu hỏi: "OverlapResolver
có phát hiện đúng nhóm chồng lấp không? SAM2 có tinh chỉnh bbox tốt hơn
hay tệ hơn bbox gốc?"

Hiển thị 2 ô cạnh nhau cho mỗi ảnh:
    Trái  — mọi Detection; box nào nằm trong 1 OverlapGroup được tô đỏ.
    Phải  — với các detection có refinement: bbox GỐC (nét đứt cam) chồng
            lên bbox ĐÃ TINH CHỈNH (nét liền xanh dương), kèm
            mask_area_ratio / refinement_confidence / used_fallback.

Cách dùng:
    python debug/02_overlap_segmentation_viewer.py
    python debug/02_overlap_segmentation_viewer.py --source path/to/anh.jpg

Controls: xem _shared/paged_viewer.py.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from matplotlib.patches import Rectangle

from debug._shared.bootstrap import get_config, resolve_data_path
from debug._shared.formatting import format_number
from debug._shared.io_utils import list_images, load_bgr, to_rgb
from debug._shared.paged_viewer import KeyboardPagedViewer

_COLOR_NORMAL = "#00C853"
_COLOR_OVERLAP_FLAGGED = "#D50000"
_COLOR_ORIGINAL_BBOX = "#FF6D00"
_COLOR_REFINED_BBOX = "#2962FF"


class OverlapSegmentationViewer(KeyboardPagedViewer):
    def __init__(self, image_paths: list[Path], detector, overlap_resolver, refiner):
        super().__init__(item_count=len(image_paths), figsize=(15, 8), window_title="Overlap & Segmentation Viewer")
        self.image_paths = image_paths
        self.detector = detector
        self.overlap_resolver = overlap_resolver
        self.refiner = refiner
        self._cache: dict[int, tuple] = {}

    def _run_stages(self, index: int):
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
            detection_result = self.detector.detect(image_data)
            overlap_result = self.overlap_resolver.resolve(detection_result)
            refinement_result = self.refiner.refine(image_bgr, detection_result, overlap_result)
            self._cache[index] = (image_bgr, detection_result, overlap_result, refinement_result)
        return self._cache[index]

    def render(self, fig, index: int) -> None:
        path = self.image_paths[index]
        image_bgr, detection_result, overlap_result, refinement_result = self._run_stages(index)
        image_rgb = to_rgb(image_bgr)

        flagged_indices: set[int] = set()
        for group in overlap_result.groups:
            flagged_indices.update(group.detection_indices)

        # ---- Ô TRÁI: Detection + cờ Overlap ----
        ax_left = fig.add_subplot(1, 2, 1)
        ax_left.imshow(image_rgb)
        ax_left.axis("off")
        for det_index, detection in enumerate(detection_result.detections):
            box = detection.bbox
            is_flagged = det_index in flagged_indices
            ax_left.add_patch(
                Rectangle(
                    (box.x1, box.y1), box.width, box.height,
                    fill=False,
                    edgecolor=_COLOR_OVERLAP_FLAGGED if is_flagged else _COLOR_NORMAL,
                    linewidth=2.5 if is_flagged else 1.5,
                )
            )
            ax_left.text(box.x1, max(box.y1 - 6, 10), f"#{det_index}", fontsize=9, color="white",
                         bbox=dict(facecolor=_COLOR_OVERLAP_FLAGGED if is_flagged else _COLOR_NORMAL, pad=2))
        ax_left.set_title(
            f"① DETECTION + ② OVERLAP\n"
            f"{len(detection_result.detections)} detection(s)  |  "
            f"{overlap_result.group_count} nhóm chồng lấp  |  "
            f"{overlap_result.overlapping_detection_count} box bị gắn cờ",
            fontsize=10,
        )

        # ---- Ô PHẢI: bbox gốc vs bbox đã tinh chỉnh ----
        ax_right = fig.add_subplot(1, 2, 2)
        ax_right.imshow(image_rgb)
        ax_right.axis("off")

        info_lines = [f"Refiner backend: {refinement_result.backend}", f"triggered: {refinement_result.triggered}", ""]

        if not refinement_result.refined_boxes:
            info_lines.append("(Không có detection nào được tinh chỉnh cho ảnh này)")
        for refined in refinement_result.refined_boxes:
            det_index = refined.detection_index
            original_box = detection_result.detections[det_index].bbox
            ax_right.add_patch(
                Rectangle((original_box.x1, original_box.y1), original_box.width, original_box.height,
                          fill=False, edgecolor=_COLOR_ORIGINAL_BBOX, linewidth=2, linestyle="--")
            )
            r = refined.refined_bbox
            ax_right.add_patch(
                Rectangle((r.x1, r.y1), r.width, r.height, fill=False, edgecolor=_COLOR_REFINED_BBOX, linewidth=2)
            )
            fallback_tag = " [FALLBACK -> giữ bbox gốc]" if refined.used_fallback else ""
            info_lines.append(
                f"#{det_index}: mask_cover={format_number(refined.mask_area_ratio, 3)} "
                f"conf={format_number(refined.refinement_confidence, 3)}{fallback_tag}"
            )

        ax_right.set_title("③ SEGMENTATION (cam nét đứt = gốc, xanh dương = đã tinh chỉnh)", fontsize=10)
        ax_right.text(
            0.02, -0.05, "\n".join(info_lines), transform=ax_right.transAxes,
            fontsize=8.5, family="monospace", va="top",
        )

        fig.suptitle(f"[{index + 1}/{len(self.image_paths)}] {path.name}", fontsize=12, fontweight="bold")
        fig.tight_layout(rect=(0, 0.08, 1, 0.95))


def main() -> None:
    parser = argparse.ArgumentParser(description="Debug giai đoạn Overlap + Segmentation (SAM2).")
    parser.add_argument("--source", type=str, default=None, help="1 ảnh hoặc 1 thư mục ảnh (mặc định: data/benchmark/images).")
    parser.add_argument("--config", type=str, default=None)
    args = parser.parse_args()

    cfg = get_config(args.config)
    source = Path(args.source) if args.source else resolve_data_path(cfg, "benchmark_images_dir")
    if not source.exists():
        source = resolve_data_path(cfg, "query_dir")

    image_paths = [source] if source.is_file() else list_images(source)
    if not image_paths:
        print(f"[ERROR] Không tìm thấy ảnh nào trong: {source}")
        return

    from src.detection.detector import Detector
    from src.pipeline.overlap import OverlapResolver
    from src.segmentation.refiner import Refiner

    detector = Detector(cfg)
    overlap_resolver = OverlapResolver(cfg)
    refiner = Refiner(cfg)

    print(f"[INFO] Nguồn ảnh: {source}  ({len(image_paths)} ảnh)")
    print(f"[INFO] refinement.enabled={cfg.refinement.enabled}  backend={cfg.refinement.backend}")

    OverlapSegmentationViewer(image_paths, detector, overlap_resolver, refiner).show()


if __name__ == "__main__":
    main()
