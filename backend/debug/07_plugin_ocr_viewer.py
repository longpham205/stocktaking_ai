"""07_plugin_ocr_viewer.py — Debug giai đoạn ⑦ PLUGIN: OCR
(engine/plugins/ocr.py), chạy qua đúng InventoryPipeline thật (cần OCR đã
được PluginManager kích hoạt cho crop đó — xem policy trong
engine/plugins/manager.py).

Chạy pipeline thật (Detect -> ... -> OCR Plugin -> Rerank) trên 1 ảnh/1
thư mục, rồi với mỗi crop có chạy OCR, hiển thị lưới 2x3:
    (0,0) Ảnh gốc + bbox detection      (0,1) Raw crop gốc
    (0,2) Kết quả OCR ở 0°              (1,0) 90°   (1,1) 180°   (1,2) 270°
mỗi ô xoay được khoanh vùng từng fragment chữ nhận diện được (xanh=giữ lại
vì đủ tin cậy/thông tin, đỏ=bị loại), góc được OCR Plugin chọn cuối cùng
viền đỏ đậm.

Đã sửa 2 lỗi so với debug_ocr.py gốc:
    - ``ct.crop.bbox`` không tồn tại trên ``CropImage`` (đúng phải là
      ``source_bbox``) -> trước đây bbox KHÔNG BAO GIỜ được vẽ lên ảnh gốc.
    - ``extract_ranks()`` cũ tham chiếu ``crop_trace.rerank_result`` (không
      tồn tại trên ``CropTrace``) -> rank_after luôn luôn là "N/A". Nay
      tính đúng: rank (trong Top-K retrieval) của SKU thắng cuộc TRƯỚC và
      SAU khi Reranker chạy, để thấy rõ khi nào OCR đổi được kết quả.

Cách dùng:
    python debug/07_plugin_ocr_viewer.py --source data/benchmark/images/mot_anh.jpg
    python debug/07_plugin_ocr_viewer.py --source data/benchmark/images --filter-ids 7,8

Controls: xem _shared/paged_viewer.py.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cv2
import numpy as np

from debug._shared.bootstrap import get_config, get_pipeline, resolve_data_path
from debug._shared.formatting import format_number, wrap_text
from debug._shared.io_utils import list_images, load_bgr, to_rgb
from debug._shared.paged_viewer import KeyboardPagedViewer

_ROTATION_GRID_POSITIONS = [(0, 2, 0), (1, 0, 90), (1, 1, 180), (1, 2, 270)]


# ---------------------------------------------------------------------------
# Helpers hình học / vẽ (giữ nguyên logic gốc từ debug_ocr.py)
# ---------------------------------------------------------------------------

def _rotate_image(image: np.ndarray, angle: int) -> np.ndarray:
    angle = int(angle) % 360
    if angle == 90:
        return cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE)
    if angle == 180:
        return cv2.rotate(image, cv2.ROTATE_180)
    if angle == 270:
        return cv2.rotate(image, cv2.ROTATE_90_COUNTERCLOCKWISE)
    return image.copy()


def _normalize_bbox(bbox) -> np.ndarray | None:
    if bbox is None:
        return None
    try:
        points = np.asarray(bbox, dtype=np.float32)
        if points.shape != (4, 2):
            return None
        return np.round(points).astype(np.int32)
    except Exception:
        return None


def _draw_ocr_fragments(image: np.ndarray, fragments: list[dict]) -> np.ndarray:
    output = image.copy()
    for index, fragment in enumerate(fragments or [], start=1):
        points = _normalize_bbox(fragment.get("bbox", []))
        if points is None:
            continue
        useful = bool(fragment.get("useful", False))
        color = (0, 255, 0) if useful else (0, 0, 255)
        status = "KEEP" if useful else "DROP"
        confidence = float(fragment.get("confidence", 0.0))
        text = str(fragment.get("text", ""))

        cv2.polylines(output, [points.reshape((-1, 1, 2))], isClosed=True, color=color, thickness=2, lineType=cv2.LINE_AA)
        label = f"[{index}] {status} {confidence:.2f} {text}"
        (tw, th), baseline = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 1)
        x, y = max(0, int(points[:, 0].min())), max(20, int(points[:, 1].min()))
        box_y1 = max(0, y - th - baseline - 4)
        cv2.rectangle(output, (x, box_y1), (min(output.shape[1] - 1, x + tw + 6), min(output.shape[0] - 1, box_y1 + th + baseline + 4)), color, -1)
        cv2.putText(output, label, (x + 3, box_y1 + th + baseline), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1, cv2.LINE_AA)
    return output


def _draw_detection_bbox(image: np.ndarray, bbox_xyxy, detection_index: int, top1: str) -> np.ndarray:
    output = image.copy()
    if bbox_xyxy is None or len(bbox_xyxy) != 4:
        return output
    x1, y1, x2, y2 = map(int, bbox_xyxy)
    h, w = output.shape[:2]
    x1, y1 = max(0, min(x1, w - 1)), max(0, min(y1, h - 1))
    x2, y2 = max(0, min(x2, w - 1)), max(0, min(y2, h - 1))
    cv2.rectangle(output, (x1, y1), (x2, y2), (0, 255, 0), 3, cv2.LINE_AA)
    label = f"Det {detection_index} | Top1: {top1}"
    (tw, th), baseline = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.8, 2)
    label_y = max(th + baseline + 5, y1 - 10)
    cv2.rectangle(output, (x1, label_y - th - baseline - 5), (x1 + tw + 10, label_y + 5), (0, 255, 0), -1)
    cv2.putText(output, label, (x1 + 5, label_y), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 2, cv2.LINE_AA)
    return output


def _reconstruct_orientation_image(raw_crop: np.ndarray, orientation: dict) -> np.ndarray | None:
    if raw_crop is None or raw_crop.size == 0:
        return None
    image_shape = orientation.get("image_shape", [])
    if not image_shape or len(image_shape) < 2:
        return None
    target_h, target_w = int(image_shape[0]), int(image_shape[1])
    angle = int(orientation.get("rotation", 0)) % 360
    base_h, base_w = (target_w, target_h) if angle in (90, 270) else (target_h, target_w)

    resized = raw_crop if raw_crop.shape[:2] == (base_h, base_w) else cv2.resize(raw_crop, (base_w, base_h), interpolation=cv2.INTER_CUBIC)
    rotated = _rotate_image(resized, angle)
    if rotated.shape[:2] != (target_h, target_w):
        rotated = cv2.resize(rotated, (target_w, target_h), interpolation=cv2.INTER_NEAREST)
    return rotated


def _rank_of(product_id: str | None, candidates) -> str:
    if product_id is None:
        return "N/A"
    match = next((c.rank for c in candidates if c.product_id == product_id), None)
    return str(match) if match is not None else "not in Top-K"


def _passes_filter(crop_trace, filter_ids: set[str] | None) -> bool:
    if not filter_ids:
        return True
    candidates = crop_trace.retrieval_result.candidates if crop_trace.retrieval_result else []
    return any(str(c.product_id) in filter_ids for c in candidates)


# ---------------------------------------------------------------------------
# Viewer
# ---------------------------------------------------------------------------

class OcrPluginViewer(KeyboardPagedViewer):
    def __init__(self, crops_data: list[dict]):
        super().__init__(item_count=len(crops_data), figsize=(16, 9), window_title="OCR Plugin Viewer")
        self.crops_data = crops_data

    def render(self, fig, index: int) -> None:
        data = self.crops_data[index]
        axes = fig.subplots(2, 3)

        def draw(ax, image_bgr, title, is_best=False):
            if image_bgr is not None and image_bgr.size > 0:
                ax.imshow(to_rgb(image_bgr) if image_bgr.ndim == 3 else image_bgr, cmap=None if image_bgr.ndim == 3 else "gray")
            ax.set_title(title, fontsize=9, fontweight="bold", color="crimson" if is_best else "black")
            ax.axis("off")
            if is_best:
                for spine in ax.spines.values():
                    spine.set_visible(True)
                    spine.set_color("crimson")
                    spine.set_linewidth(3)

        ocr = data["ocr_evidence"]
        title_main = (
            f"[{index + 1}/{len(self.crops_data)}] {data['filename']} | Det {data['det_idx']} | Top1: {data['top1']}\n"
            f"Rank Top-K trước rerank: {data['rank_before']}  ->  sau rerank: {data['rank_after']}\n"
            f"OCR text: {wrap_text(ocr.get('text', ''), 60)!r} | rotation={ocr.get('rotation', 0)}° | "
            f"conf={format_number(ocr.get('confidence'), 2)} | useful_frags={ocr.get('useful_fragment_count', 0)}"
        )
        fig.suptitle(title_main, fontsize=10.5, fontweight="bold", color="navy")

        draw(axes[0, 0], data["orig_img"], "1. Ảnh gốc + bbox detection")
        draw(axes[0, 1], data["raw_crop"], f"2. Raw crop [{data['raw_w']}x{data['raw_h']}]")

        rotations_by_angle = {r["angle"]: r for r in data["rotations"]}
        for r, c, angle in _ROTATION_GRID_POSITIONS:
            info = rotations_by_angle.get(angle)
            ax = axes[r, c]
            if info and info["img"] is not None:
                is_best = angle == data["best_rotation"]
                title = f"Rotate {angle}°{' [BEST]' if is_best else ''} | score={format_number(info['score'], 3)}\n{wrap_text(info['text'], 28)!r}"
                draw(ax, info["img"], title, is_best=is_best)
            else:
                ax.axis("off")
                ax.set_title(f"Rotate {angle}° (N/A)", fontsize=9)

        fig.tight_layout(rect=(0, 0.02, 1, 0.88))


def _collect_crops_data(pipeline, image_paths: list[Path], filter_ids: set[str] | None) -> list[dict]:
    crops_data: list[dict] = []
    from engine.core.utils import generate_id
    from engine.models.models import ImageData

    for image_path in image_paths:
        image_bgr = load_bgr(image_path)
        image_data = ImageData(
            image_id=generate_id(), source_path=str(image_path), image_array=image_bgr,
            width=image_bgr.shape[1], height=image_bgr.shape[0],
        )
        print(f"[RUN] {image_path.name}")
        _, trace = pipeline.run_with_trace(image_data)

        for crop_trace in trace.crops:
            if not crop_trace.plugin_result or "ocr" not in crop_trace.plugin_result.executed_plugins:
                continue
            if not _passes_filter(crop_trace, filter_ids):
                continue

            raw_crop = crop_trace.crop.raw_image_array
            if raw_crop is None or raw_crop.size == 0:
                continue

            top1 = str(crop_trace.retrieval_result.top_candidate.product_id) if crop_trace.retrieval_result.top_candidate else "None"
            candidates = crop_trace.retrieval_result.candidates
            rank_before = _rank_of(crop_trace.decision_result.product_id, candidates)
            rank_after = _rank_of(crop_trace.final_decision.product_id, candidates)

            ocr_evidence = crop_trace.plugin_result.evidence.get("ocr", {})
            orientation_scores = ocr_evidence.get("orientation_scores", [])
            best_rotation = int(ocr_evidence.get("rotation", 0))

            rotations = []
            for angle in (0, 90, 180, 270):
                orientation = next((o for o in orientation_scores if int(o.get("rotation", 0)) == angle), None)
                if orientation is None:
                    rotations.append({"angle": angle, "img": None, "score": 0.0, "text": "N/A"})
                    continue
                rotated = _reconstruct_orientation_image(raw_crop, orientation)
                img = _draw_ocr_fragments(rotated, orientation.get("fragments", [])) if rotated is not None else None
                rotations.append({"angle": angle, "img": img, "score": float(orientation.get("score", 0.0)), "text": orientation.get("text", "")})

            bbox_xyxy = crop_trace.crop.source_bbox.as_list()
            orig_disp = _draw_detection_bbox(image_bgr, bbox_xyxy, crop_trace.crop.detection_index, top1)

            crops_data.append({
                "filename": image_path.name, "det_idx": crop_trace.crop.detection_index, "top1": top1,
                "rank_before": rank_before, "rank_after": rank_after,
                "orig_img": orig_disp, "raw_crop": raw_crop,
                "raw_w": raw_crop.shape[1], "raw_h": raw_crop.shape[0],
                "best_rotation": best_rotation, "ocr_evidence": ocr_evidence, "rotations": rotations,
            })

    return crops_data


def main() -> None:
    parser = argparse.ArgumentParser(description="Debug giai đoạn Plugin OCR trên pipeline thật.")
    parser.add_argument("--source", type=str, default=None, help="1 ảnh hoặc 1 thư mục ảnh (mặc định: data/benchmark/images).")
    parser.add_argument("--filter-ids", type=str, default="", help='Chỉ hiện crop có SKU này trong Top-K, VD "7,8". Rỗng = không lọc.')
    parser.add_argument("--config", type=str, default=None)
    args = parser.parse_args()

    cfg = get_config(args.config)
    pipeline = get_pipeline(args.config)
    source = Path(args.source) if args.source else resolve_data_path(cfg, "benchmark_images_dir")
    image_paths = [source] if source.is_file() else list_images(source)
    filter_ids = {pid.strip() for pid in args.filter_ids.split(",") if pid.strip()} or None

    if not image_paths:
        print(f"[ERROR] Không tìm thấy ảnh trong: {source}")
        return

    crops_data = _collect_crops_data(pipeline, image_paths, filter_ids)
    print(f"\n[INFO] {len(crops_data)} crop có chạy OCR Plugin (trong {len(image_paths)} ảnh).")
    for i, d in enumerate(crops_data, start=1):
        print(f"  [{i:02d}] {d['filename']} det#{d['det_idx']} top1={d['top1']} rank {d['rank_before']}->{d['rank_after']} "
              f"text={d['ocr_evidence'].get('text', '')!r}")

    OcrPluginViewer(crops_data).show()


if __name__ == "__main__":
    main()
