"""10_reranker_viewer.py — Debug giai đoạn ⑧ RERANKER (src/decision/reranker.py).

So với debug_reranker.py gốc, file này thay đổi 2 điều:

    1. UX: gốc dùng ``plt.show()`` CHẶN TUẦN TỰ — phải đóng cửa sổ crop
       này mới xem được crop kế tiếp, không có phím lướt qua lại như các
       viewer khác (OCR/Color/Benchmark). Nay dùng chung
       ``KeyboardPagedViewer`` — bấm ←/→ lướt qua mọi crop cần debug.
    2. Dữ liệu: gốc đọc ``final_result.rerank_debug`` — thuộc tính này
       KHÔNG TỒN TẠI trên ``DecisionResult`` (xem models.py) -> mọi bảng
       "candidates/rank_before/rank_after" trong file gốc LUÔN LUÔN RỖNG,
       và lưới so sánh Top-K luôn hiện "No Gallery Image" cho toàn bộ 5 ô
       ứng viên vì ``rerank_candidates`` luôn là ``[]``. Bản này bỏ hẳn
       field không tồn tại đó, thay bằng dữ liệu THẬT sẵn có: Top-K từ
       ``retrieval_result`` (trước rerank) + ``decision_result``/
       ``final_decision`` (sau rerank) + ``plugin_result.evidence`` — vẫn
       trả lời đúng câu hỏi gốc muốn hỏi ("Reranker có đổi Top-1 không,
       đổi nhờ bằng chứng nào"), chỉ khác nguồn dữ liệu.

Cách dùng:
    python debug/10_reranker_viewer.py --source data/query/3.jpg
    python debug/10_reranker_viewer.py --source data/benchmark/images --filter-ids 5,6

Controls: xem _shared/paged_viewer.py.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from debug._shared.bootstrap import gallery_folder_of, get_config, get_pipeline, resolve_data_path
from debug._shared.formatting import format_number
from debug._shared.io_utils import crop_to_object, list_images, load_bgr, to_rgb
from debug._shared.paged_viewer import KeyboardPagedViewer


def _find_gallery_reference(cfg, product_id: str | None) -> Path | None:
    if not product_id:
        return None
    folder_name = gallery_folder_of(cfg, str(product_id))
    if not folder_name:
        return None
    images = list_images(resolve_data_path(cfg, "gallery_dir") / folder_name)
    return images[0] if images else None


class RerankerViewer(KeyboardPagedViewer):
    def __init__(self, crops_data: list[dict], cfg, top_k_display: int):
        super().__init__(item_count=len(crops_data), figsize=(18, 9), window_title="Reranker Viewer")
        self.crops_data = crops_data
        self.cfg = cfg
        self.top_k_display = top_k_display

    def render(self, fig, index: int) -> None:
        data = self.crops_data[index]
        grid = fig.add_gridspec(2, self.top_k_display + 1, width_ratios=[1.3] + [1] * self.top_k_display)

        # ---- Ảnh: crop gốc + Top-K gallery reference ----
        ax_crop = fig.add_subplot(grid[0, 0])
        ax_crop.imshow(to_rgb(data["raw_crop"]))
        ax_crop.set_title(f"CROP #{data['det_idx']}\n(query)", fontsize=10, fontweight="bold", color="blue")
        ax_crop.axis("off")

        for rank_pos in range(self.top_k_display):
            ax = fig.add_subplot(grid[0, rank_pos + 1])
            if rank_pos < len(data["candidates"]):
                candidate = data["candidates"][rank_pos]
                ref_path = _find_gallery_reference(self.cfg, candidate.product_id)
                is_winner = candidate.product_id == data["final_product_id"]
                if ref_path is not None:
                    # Ảnh gallery chụp xa (vd. Kem ABA/ABC) -> cắt sát vật để dễ so với crop.
                    ax.imshow(to_rgb(crop_to_object(load_bgr(ref_path))))
                else:
                    ax.text(0.5, 0.5, f"ID {candidate.product_id}\n(không có ảnh gallery)", ha="center", va="center", fontsize=9)
                title = f"#{candidate.rank} ID={candidate.product_id}\nsim={format_number(candidate.similarity_score, 3)}"
                if is_winner:
                    title += "\n★ FINAL"
                ax.set_title(title, fontsize=9, fontweight="bold", color="green" if is_winner else "black")
            ax.axis("off")

        # ---- Text: Top-K trước rerank / quyết định sau rerank ----
        ax_before = fig.add_subplot(grid[1, : (self.top_k_display + 1) // 2])
        ax_before.axis("off")
        lines_before = ["RETRIEVAL TOP-K (trước Reranker)", ""]
        for c in data["candidates"]:
            lines_before.append(f"  [{c.rank}] ID={c.product_id} sim={format_number(c.similarity_score, 4)}  {c.product_name}")
        ax_before.text(0.0, 1.0, "\n".join(lines_before), va="top", ha="left", fontsize=9, family="monospace", transform=ax_before.transAxes)

        ax_after = fig.add_subplot(grid[1, (self.top_k_display + 1) // 2 :])
        ax_after.axis("off")
        dr, fd = data["decision_result"], data["final_decision"]
        changed = dr.product_id != fd.product_id
        lines_after = [
            "QUYẾT ĐỊNH", "",
            f"  Trước rerank : product={dr.product_id}  status={dr.status}  sim={format_number(dr.similarity_score, 4)}",
            f"  Sau rerank   : product={fd.product_id}  status={fd.status}  final_conf={format_number(fd.final_confidence, 4)}",
            f"  Top-1 {'ĐÃ ĐỔI' if changed else 'không đổi'}",
            f"  Plugin đã chạy: {', '.join(data['executed_plugins']) or '(không)'}",
            f"  reason: {fd.reason}",
        ]
        ax_after.text(0.0, 1.0, "\n".join(lines_after), va="top", ha="left", fontsize=9, family="monospace", transform=ax_after.transAxes)

        fig.suptitle(f"[{index + 1}/{len(self.crops_data)}] {data['filename']} — Crop #{data['det_idx']}", fontsize=13, fontweight="bold")
        fig.tight_layout(rect=(0, 0, 1, 0.93))


def _collect_crops_data(pipeline, image_paths: list[Path], filter_ids: set[str] | None, top_k_display: int) -> list[dict]:
    crops_data: list[dict] = []
    from src.core.utils import generate_id
    from src.models.models import ImageData

    for image_path in image_paths:
        image_bgr = load_bgr(image_path)
        image_data = ImageData(
            image_id=generate_id(), source_path=str(image_path), image_array=image_bgr,
            width=image_bgr.shape[1], height=image_bgr.shape[0],
        )
        print(f"[RUN] {image_path.name}")
        _, trace = pipeline.run_with_trace(image_data)

        for crop_trace in trace.crops:
            if not crop_trace.plugin_result:  # chỉ crop có chạy plugin mới thực sự đi qua Reranker
                continue
            candidates = crop_trace.retrieval_result.candidates[:top_k_display]
            if filter_ids and not any(str(c.product_id) in filter_ids for c in candidates):
                continue

            raw_crop = crop_trace.crop.raw_image_array
            if raw_crop is None or raw_crop.size == 0:
                continue

            crops_data.append({
                "filename": image_path.name, "det_idx": crop_trace.crop.detection_index,
                "raw_crop": raw_crop, "candidates": candidates,
                "decision_result": crop_trace.decision_result, "final_decision": crop_trace.final_decision,
                "final_product_id": crop_trace.final_decision.product_id,
                "executed_plugins": crop_trace.plugin_result.executed_plugins,
            })

    return crops_data


def main() -> None:
    parser = argparse.ArgumentParser(description="Debug giai đoạn Reranker (before/after, so với gallery reference).")
    parser.add_argument("--source", type=str, default=None, help="1 ảnh hoặc 1 thư mục ảnh (mặc định: data/query).")
    parser.add_argument("--filter-ids", type=str, default="", help='Chỉ hiện crop có SKU này trong Top-K, VD "5,6".')
    parser.add_argument("--top-k-display", type=int, default=5)
    parser.add_argument("--config", type=str, default=None)
    args = parser.parse_args()

    cfg = get_config(args.config)
    pipeline = get_pipeline(args.config)
    source = Path(args.source) if args.source else resolve_data_path(cfg, "query_dir")
    image_paths = [source] if source.is_file() else list_images(source)
    filter_ids = {pid.strip() for pid in args.filter_ids.split(",") if pid.strip()} or None

    if not image_paths:
        print(f"[ERROR] Không tìm thấy ảnh trong: {source}")
        return

    crops_data = _collect_crops_data(pipeline, image_paths, filter_ids, args.top_k_display)
    changed = sum(1 for d in crops_data if d["decision_result"].product_id != d["final_decision"].product_id)
    print(f"\n[INFO] {len(crops_data)} crop đi qua Reranker (trong {len(image_paths)} ảnh); Top-1 bị đổi ở {changed} crop.")

    RerankerViewer(crops_data, cfg, args.top_k_display).show()


if __name__ == "__main__":
    main()
