"""06_decision_trigger_report.py — Debug giai đoạn ⑥ DECISION
(src/decision/decision.py), TRƯỚC khi Reranker chạm vào.

Chạy `InventoryPipeline.run_with_trace()` trên toàn bộ ảnh benchmark, rồi
so sánh:
    - `crop_trace.decision_result` : output THÔ của `DecisionEngine.decide()`
      (accepted / uncertain / rejected + trigger_reasons), TRƯỚC
      PluginManager/Reranker.
    - `crop_trace.final_decision`  : output CUỐI CÙNG sau Reranker.

Trả lời 2 câu hỏi mà report JSON của VAL không tự trả lời được:
    1. `trigger_reasons` có thực sự chứa "uncertain" không (hay ngưỡng
       band đang không bắt được ca nào)?
    2. Bao nhiêu ca "uncertain" thô bị Reranker âm thầm nâng lên
       "accepted" — giải thích vì sao eval.uncertain_case_count có thể = 0
       dù DecisionEngine hoạt động đúng.

Cách dùng:
    python debug/06_decision_trigger_report.py
    python debug/06_decision_trigger_report.py --watch 7,8 --images "data/benchmark/images/*.jpg"
"""

from __future__ import annotations

import argparse
import glob
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from debug._shared.bootstrap import get_config, get_pipeline, resolve_data_path
from debug._shared.formatting import format_number
from debug._shared.io_utils import load_bgr


def main() -> None:
    parser = argparse.ArgumentParser(description="Phân tích DecisionEngine raw vs final (post-Reranker).")
    parser.add_argument("--images", type=str, default=None, help='Glob pattern ảnh benchmark (mặc định: "<benchmark_images_dir>/*.jpg").')
    parser.add_argument("--watch", type=str, default="", help='Danh sách product_id cần in chi tiết từng crop, cách nhau dấu phẩy. Ví dụ: "7,8". Rỗng = không theo dõi.')
    parser.add_argument("--config", type=str, default=None)
    args = parser.parse_args()

    cfg = get_config(args.config)
    pipeline = get_pipeline(args.config)

    images_glob = args.images or str(resolve_data_path(cfg, "benchmark_images_dir") / "*.jpg")
    watch_ids = {pid.strip() for pid in args.watch.split(",") if pid.strip()}

    lower_band = cfg.decision.similarity_threshold - cfg.decision.uncertain_band
    print(
        f"similarity_threshold={cfg.decision.similarity_threshold} "
        f"uncertain_band={cfg.decision.uncertain_band} -> lower_band={lower_band:.4f} "
        f"min_confidence_accept={cfg.decision.min_confidence_accept} "
        f"ambiguous_top_n={cfg.decision.ambiguous_top_n} ambiguous_margin={cfg.decision.ambiguous_margin}\n"
    )

    raw_status_counter: Counter = Counter()
    reason_counter: Counter = Counter()
    upgrade_counter: Counter = Counter()
    near_band_edge: list[tuple] = []
    watch_rows: list[dict] = []

    image_paths = sorted(glob.glob(images_glob))
    if not image_paths:
        print(f"[ERROR] Không tìm thấy ảnh nào khớp pattern: {images_glob}")
        return

    from src.core.utils import generate_id
    from src.models.models import ImageData

    for path in image_paths:
        image_bgr = load_bgr(path)
        image_data = ImageData(
            image_id=generate_id(), source_path=path, image_array=image_bgr,
            width=image_bgr.shape[1], height=image_bgr.shape[0],
        )
        _, trace = pipeline.run_with_trace(image_data)

        for crop_trace in trace.crops:
            dr = crop_trace.decision_result
            fd = crop_trace.final_decision

            if dr is None or dr.product_id is None:
                raw_status_counter["no_candidate"] += 1
                continue

            raw_status_counter[dr.status] += 1
            reasons = dr.trigger_reasons or frozenset()
            for reason in (reasons or ("<none>",)):
                reason_counter[reason] += 1

            if fd is not None:
                upgrade_counter[(dr.status, fd.status)] += 1

            sim = dr.similarity_score
            if lower_band - 0.02 <= sim < lower_band + 0.02:
                near_band_edge.append((path, crop_trace.crop.detection_index, sim, dr.status))

            if watch_ids and dr.product_id in watch_ids:
                watch_rows.append({
                    "path": path, "idx": crop_trace.crop.detection_index, "product_id": dr.product_id,
                    "similarity": sim, "detection_conf": dr.detection_confidence, "final_confidence": dr.final_confidence,
                    "raw_status": dr.status, "reasons": sorted(reasons), "forced_plugins": sorted(dr.forced_plugins or ()),
                    "final_status": fd.status if fd else None, "final_product_id": fd.product_id if fd else None,
                    "retrieval_top1_before_rerank": crop_trace.retrieval_result.top_candidate.product_id if crop_trace.retrieval_result.top_candidate else None,
                })

    print("=== DecisionEngine raw status distribution (pre-plugin, pre-rerank) ===")
    for status, count in raw_status_counter.most_common():
        print(f"  {status:12s}: {count}")

    print("\n=== DecisionEngine trigger_reasons distribution ===")
    for reason, count in reason_counter.most_common():
        print(f"  {reason:12s}: {count}")

    print("\n=== raw_status -> final_status transitions (sau Reranker) ===")
    for (raw, final), count in sorted(upgrade_counter.items(), key=lambda kv: -kv[1]):
        flag = "  <-- upgrade uncertain->accepted" if raw == "uncertain" and final == "accepted" else ""
        print(f"  {raw:10s} -> {final:10s}: {count}{flag}")

    print(f"\n=== Crop có similarity trong +/-0.02 quanh lower_band={lower_band:.4f} ===")
    for path, idx, sim, status in near_band_edge:
        print(f"  {path} crop#{idx} sim={format_number(sim, 4)} status={status}")

    if watch_ids:
        print(f"\n=== Chi tiết từng crop cho product_id trong {sorted(watch_ids)} ===")
        for row in watch_rows:
            print(
                f"  {row['path']} crop#{row['idx']} product={row['product_id']} "
                f"sim={format_number(row['similarity'], 4)} det_conf={format_number(row['detection_conf'], 3)} "
                f"final_conf={format_number(row['final_confidence'], 3)} raw_status={row['raw_status']} "
                f"reasons={row['reasons']} forced={row['forced_plugins']} "
                f"-> final_status={row['final_status']} final_product={row['final_product_id']} "
                f"retrieval_top1={row['retrieval_top1_before_rerank']}"
            )


if __name__ == "__main__":
    main()
