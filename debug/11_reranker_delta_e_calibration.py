"""11_reranker_delta_e_calibration.py — Hiệu chuẩn rerank.color.delta_e_strong
/ delta_e_weak (src/decision/reranker.py) bằng dữ liệu benchmark thật.

Với mỗi GT annotation match được 1 crop (theo IoU), tính:
    - ΔE giữa màu đo được của crop và màu tham chiếu của ĐÚNG SKU (GT)
    - ΔE giữa màu đo được và màu tham chiếu của SKU SAI gần nhất
Rồi đề xuất `delta_e_strong` (p75 của phân phối ĐÚNG) và `delta_e_weak`
(p25 của phân phối SAI gần nhất) dựa trên dữ liệu thật thay vì đoán tay.

ĐÃ SỬA LỖI so với debug_delta_e.py gốc: bản gốc đọc
``final_decision.rerank_debug`` — thuộc tính này KHÔNG TỒN TẠI trên
``DecisionResult`` (xem src/models/models.py) -> ``skipped_no_rerank_debug``
tăng ở MỌI crop, "THỐNG KÊ" cuối cùng luôn rỗng, không bao giờ ra được đề
xuất ngưỡng nào. Bản này gọi THẲNG 2 hàm nội bộ thật của chính Reranker
đang chạy trong pipeline (``Reranker._extract_color_lab`` và
``Reranker._calculate_color_distances``) — vừa sửa đúng lỗi, vừa đảm bảo
số liệu hiệu chuẩn khớp 100% với công thức CIEDE2000 + color-code
resolution thật (không phải bản viết lại tay có thể lệch dần theo thời gian).

Giả định (giữ nguyên từ bản gốc, tự chỉnh trong resolve_gt_product_id()
nếu benchmark của bạn khác):
    category["name"] trong COCO khớp với GIÁ TRỊ trong
    thư mục gallery trong catalog, HOẶC khớp trực tiếp với product_id dạng số.

Cách dùng:
    python debug/11_reranker_delta_e_calibration.py
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np

from debug._shared.bootstrap import get_catalog, get_config, get_pipeline, resolve_data_path
from debug._shared.io_utils import list_images, load_bgr


def _iou_xywh_vs_xyxy(gt_bbox_xywh, pred_bbox_xyxy) -> float:
    gx, gy, gw, gh = gt_bbox_xywh
    gt_x1, gt_y1, gt_x2, gt_y2 = gx, gy, gx + gw, gy + gh
    px1, py1, px2, py2 = pred_bbox_xyxy
    ix1, iy1 = max(gt_x1, px1), max(gt_y1, py1)
    ix2, iy2 = min(gt_x2, px2), min(gt_y2, py2)
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    union = max(0.0, gw * gh) + max(0.0, (px2 - px1) * (py2 - py1)) - inter
    return inter / union if union > 0 else 0.0


def main() -> None:
    parser = argparse.ArgumentParser(description="Hiệu chuẩn ngưỡng ΔE của Reranker từ dữ liệu benchmark.")
    parser.add_argument("--config", type=str, default=None)
    args = parser.parse_args()

    cfg = get_config(args.config)
    pipeline = get_pipeline(args.config)
    reranker = pipeline._reranker  # noqa: SLF001 - cố ý dùng đúng instance thật, không viết lại logic
    from src.decision.reranker import Reranker

    coco_path = resolve_data_path(cfg, "benchmark_labels_dir")
    images_dir = resolve_data_path(cfg, "benchmark_images_dir")
    iou_match_threshold = float(cfg.validation.iou_match_threshold)

    if not coco_path.is_file():
        print(f"[ERROR] Không tìm thấy COCO annotations: {coco_path}")
        return

    with coco_path.open("r", encoding="utf-8") as f:
        coco = json.load(f)

    image_id_to_filename = {img["id"]: img["file_name"] for img in coco.get("images", [])}
    category_id_to_name = {cat["id"]: str(cat.get("name", "")) for cat in coco.get("categories", [])}
    annotations_by_image_id: dict[int, list[dict]] = defaultdict(list)
    for ann in coco.get("annotations", []):
        annotations_by_image_id[ann["image_id"]].append(ann)

    print(f"COCO: {len(image_id_to_filename)} ảnh, {sum(len(v) for v in annotations_by_image_id.values())} annotation")

    catalog = get_catalog(cfg)
    known_ids = set(catalog.products())
    name_to_product_id = catalog.folder_to_product_id()  # tên thư mục gallery -> product_id

    def resolve_gt_product_id(category_id: int) -> str | None:
        cat_name = category_id_to_name.get(category_id)
        if cat_name is None:
            return None
        if cat_name in name_to_product_id:
            return name_to_product_id[cat_name]
        if cat_name in known_ids:
            return cat_name
        return None

    correct_delta_es: list[float] = []
    confusing_delta_es: list[float] = []
    mismatch_details: list[dict] = []
    query_lab_by_product: dict[str, list[list[float]]] = defaultdict(list)

    skipped = {"no_gt_code": 0, "no_match": 0, "no_color_evidence": 0, "unresolved_category": 0}

    image_paths = list_images(images_dir)
    print(f"Quét {len(image_paths)} ảnh benchmark...")

    from src.core.utils import generate_id
    from src.models.models import ImageData

    for image_index, image_path in enumerate(image_paths, start=1):
        matching_image_id = next((img_id for img_id, name in image_id_to_filename.items() if name == image_path.name), None)
        if matching_image_id is None:
            continue
        gt_annotations = annotations_by_image_id.get(matching_image_id, [])
        if not gt_annotations:
            continue

        image_bgr = load_bgr(image_path)
        image_data = ImageData(
            image_id=generate_id(), source_path=str(image_path), image_array=image_bgr,
            width=image_bgr.shape[1], height=image_bgr.shape[0],
        )
        _, trace = pipeline.run_with_trace(image_data)

        for gt_ann in gt_annotations:
            gt_product_id = resolve_gt_product_id(gt_ann["category_id"])
            if gt_product_id is None:
                skipped["unresolved_category"] += 1
                continue

            best_crop, best_iou = None, 0.0
            for crop_trace in trace.crops:
                box = crop_trace.crop.source_bbox
                iou = _iou_xywh_vs_xyxy(gt_ann["bbox"], (box.x1, box.y1, box.x2, box.y2))
                if iou > best_iou:
                    best_iou, best_crop = iou, crop_trace

            if best_crop is None or best_iou < iou_match_threshold:
                skipped["no_match"] += 1
                continue

            if not best_crop.plugin_result or "color" not in best_crop.plugin_result.executed_plugins:
                skipped["no_color_evidence"] += 1
                continue

            query_lab = Reranker._extract_color_lab(best_crop.plugin_result)  # noqa: SLF001
            if query_lab is None:
                skipped["no_color_evidence"] += 1
                continue

            candidates = best_crop.retrieval_result.candidates
            distances = reranker._calculate_color_distances(candidates, query_lab)  # noqa: SLF001

            correct_de = distances.get(str(gt_product_id))
            if correct_de is None:
                skipped["no_gt_code"] += 1
                continue

            correct_delta_es.append(correct_de)
            query_lab_by_product[str(gt_product_id)].append(query_lab)

            wrong_des = {pid: de for pid, de in distances.items() if pid != str(gt_product_id)}
            if wrong_des:
                nearest_wrong_pid = min(wrong_des, key=wrong_des.get)
                nearest_wrong_de = wrong_des[nearest_wrong_pid]
                confusing_delta_es.append(nearest_wrong_de)
                if nearest_wrong_de < correct_de:
                    mismatch_details.append({
                        "image": image_path.name, "gt_product_id": gt_product_id, "correct_de": correct_de,
                        "wrong_product_id": nearest_wrong_pid, "wrong_de": nearest_wrong_de,
                    })

        if image_index % 20 == 0:
            print(f"  ...{image_index}/{len(image_paths)} ảnh")

    print("\n" + "=" * 70)
    print("THỐNG KÊ")
    print("=" * 70)
    print(f"Số crop match GT đúng + có color code   : {len(correct_delta_es)}")
    print(f"Bỏ qua (GT không có color code)         : {skipped['no_gt_code']}")
    print(f"Bỏ qua (không match IoU với GT)         : {skipped['no_match']}")
    print(f"Bỏ qua (crop không chạy Color Plugin)   : {skipped['no_color_evidence']}")
    print(f"Bỏ qua (category không resolve được)    : {skipped['unresolved_category']}")

    if correct_delta_es:
        arr = np.array(correct_delta_es)
        print(f"\nΔE của candidate ĐÚNG (query vs GT color code):")
        print(f"  mean={arr.mean():.2f}  median={np.median(arr):.2f}  p75={np.percentile(arr, 75):.2f}  "
              f"p90={np.percentile(arr, 90):.2f}  max={arr.max():.2f}")

    if confusing_delta_es:
        arr2 = np.array(confusing_delta_es)
        print(f"\nΔE của candidate SAI gần nhất (nearest wrong):")
        print(f"  mean={arr2.mean():.2f}  median={np.median(arr2):.2f}  p25={np.percentile(arr2, 25):.2f}  "
              f"p10={np.percentile(arr2, 10):.2f}  min={arr2.min():.2f}")

    print(f"\nSố case wrong_ΔE < correct_ΔE (nguy cơ gây nhầm): {len(mismatch_details)}")
    for d in mismatch_details[:20]:
        print(f"  {d['image']}: GT={d['gt_product_id']} correct_ΔE={d['correct_de']:.2f} vs "
              f"SAI={d['wrong_product_id']} ΔE={d['wrong_de']:.2f}")

    if correct_delta_es and confusing_delta_es:
        suggested_strong = round(float(np.percentile(correct_delta_es, 75)), 1)
        suggested_weak = round(float(np.percentile(confusing_delta_es, 25)), 1)
        print("\n" + "=" * 70)
        print(f"ĐỀ XUẤT (p75 của ĐÚNG / p25 của SAI gần nhất) — hiện tại trong config: "
              f"strong={cfg.rerank.color.delta_e_strong} weak={cfg.rerank.color.delta_e_weak}")
        print("=" * 70)
        print(f"delta_e_strong: {suggested_strong}")
        print(f"delta_e_weak:   {suggested_weak}")
        if suggested_weak <= suggested_strong:
            print("[CẢNH BÁO] weak <= strong -- 2 phân phối chồng lấn nhiều, cần xem lại reference/ROI trước khi tin threshold này.")

    if query_lab_by_product:
        print("\n" + "=" * 70)
        print("REFERENCE THỰC NGHIỆM (mean representative_lab đo được, đã qua L-dampening)")
        print("=" * 70)
        for pid, lab_list in query_lab_by_product.items():
            arr = np.array(lab_list)
            print(f"product {pid}: n={len(lab_list)} mean_lab=[{arr.mean(axis=0)[0]:.1f},{arr.mean(axis=0)[1]:.1f},{arr.mean(axis=0)[2]:.1f}] "
                  f"std_lab=[{arr.std(axis=0)[0]:.1f},{arr.std(axis=0)[1]:.1f},{arr.std(axis=0)[2]:.1f}]")


if __name__ == "__main__":
    main()
