"""08_plugin_color_viewer.py — Debug giai đoạn ⑦ PLUGIN: Color
(engine/plugins/color.py), chạy qua InventoryPipeline thật.

Chạy pipeline thật trên 1 ảnh/thư mục, với mỗi crop có chạy Color Plugin,
hiển thị lưới 2x5 tái dựng lại đúng 8 bước xử lý nội bộ của plugin (resize
-> gray -> blur -> Canny+morphology -> contour ứng viên hình chữ nhật ->
ROI cuối cùng -> ROI đã crop -> highlight mask) + panel màu chủ đạo (HEX/
RGB/Lab/Chroma) + panel K-Means palette.

Đã sửa 2 lỗi so với debug_color.py gốc:
    - Code cũ dò ``pipeline.plugin_manager.plugins`` (dict) để gọi đúng
      hàm ``_resize_for_detection`` thật của ColorPlugin, nhưng thuộc
      tính thật là ``pipeline._plugin_manager._plugins`` (LIST, không
      phải dict) -> nhánh "gọi hàm thật" không bao giờ chạy, luôn rơi vào
      bản tự viết lại thủ công (dễ lệch dần với code thật theo thời gian).
    - ``final_result.rerank_debug`` không tồn tại trên ``DecisionResult``
      -> rank_before/rank_after luôn là "N/A". Nay tính đúng bằng cách
      tra rank trong Top-K retrieval gốc, giống 07_plugin_ocr_viewer.py.

Cách dùng:
    python debug/08_plugin_color_viewer.py --source data/query/anh.jpg
    python debug/08_plugin_color_viewer.py --source data/benchmark/images --filter-ids 5,6

Controls: xem _shared/paged_viewer.py.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cv2
import numpy as np
from matplotlib.patches import Rectangle

from debug._shared.bootstrap import get_config, get_pipeline, resolve_data_path
from debug._shared.formatting import format_number
from debug._shared.io_utils import list_images, load_bgr, to_rgb
from debug._shared.paged_viewer import KeyboardPagedViewer


# ---------------------------------------------------------------------------
# Helpers màu sắc (giữ nguyên logic gốc)
# ---------------------------------------------------------------------------

def _dominant_color_info(hex_color: str | None) -> dict | None:
    if not hex_color:
        return None
    try:
        h = str(hex_color).strip().lstrip("#")
        if len(h) != 6:
            return None
        r, g, b = (int(h[i : i + 2], 16) for i in (0, 2, 4))
        lab = cv2.cvtColor(np.uint8([[[b, g, r]]]), cv2.COLOR_BGR2LAB)[0, 0].astype(float)
        L, a, b_lab = lab[0] * 100.0 / 255.0, lab[1] - 128.0, lab[2] - 128.0
        return {"hex": f"#{h.upper()}", "rgb": (r, g, b), "lab": (L, a, b_lab), "chroma": float(np.hypot(a, b_lab))}
    except Exception:
        return None


def _hex_to_rgb01(hex_color: str | None) -> tuple[float, float, float]:
    info = _dominant_color_info(hex_color)
    return tuple(v / 255.0 for v in info["rgb"]) if info else (0.5, 0.5, 0.5)


def _draw_palette_bar(ax, palette: list[str], percentages: list[float]) -> None:
    if not palette or not percentages:
        ax.text(0.5, 0.5, "no palette", ha="center", va="center")
        ax.axis("off")
        return
    left = 0.0
    for color, pct in zip(palette, percentages):
        pct = float(pct)
        info = _dominant_color_info(color)
        ax.barh(0, pct, left=left, color=_hex_to_rgb01(color), edgecolor="black", height=0.6)
        center = left + pct / 2.0
        ax.text(center, 0.18, f"{color}  {pct * 100:.1f}%", ha="center", va="center", fontsize=8, fontweight="bold")
        if info:
            L, a, b = info["lab"]
            ax.text(center, -0.02, f"RGB {info['rgb']}", ha="center", va="center", fontsize=7)
            ax.text(center, -0.17, f"Lab ({L:.1f}, {a:.1f}, {b:.1f})", ha="center", va="center", fontsize=7)
            ax.text(center, -0.32, f"C {info['chroma']:.1f}", ha="center", va="center", fontsize=7)
        left += pct
    ax.set_xlim(0, max(left, 1e-6))
    ax.set_ylim(-0.45, 0.45)
    ax.set_yticks([])
    ax.set_title("K-Means palette", fontsize=10, fontweight="bold")


def _find_color_plugin(pipeline):
    """Tìm đúng instance ColorPlugin thật đang chạy trong pipeline (để tái dùng
    nguyên hàm resize thật thay vì viết lại thủ công)."""
    try:
        for plugin in pipeline._plugin_manager._plugins:  # noqa: SLF001 - công cụ debug, cố ý đọc nội bộ
            if getattr(plugin, "name", None) == "color":
                return plugin
    except AttributeError:
        pass
    return None


def _resize_like_plugin(color_plugin, raw_image: np.ndarray, target_size: int) -> np.ndarray:
    if color_plugin is not None and hasattr(color_plugin, "_resize_for_detection"):
        return color_plugin._resize_for_detection(raw_image, target_size)  # noqa: SLF001
    h, w = raw_image.shape[:2]
    scale = target_size / max(h, w)
    if scale >= 1:
        return raw_image.copy()
    return cv2.resize(raw_image, (max(2, round(w * scale)), max(2, round(h * scale))), interpolation=cv2.INTER_AREA)


def _build_edge_visual(raw_image: np.ndarray, color_cfg, color_plugin) -> dict | None:
    try:
        detection_image = _resize_like_plugin(color_plugin, raw_image, int(color_cfg.clustering_size))
        gray = cv2.cvtColor(detection_image, cv2.COLOR_BGR2GRAY)
        blur_k = max(3, int(color_cfg.blur_kernel_size) | 1)
        blurred = cv2.GaussianBlur(gray, (blur_k, blur_k), 0)
        edges = cv2.Canny(blurred, int(color_cfg.canny_threshold1), int(color_cfg.canny_threshold2))
        edges_closed = edges
        if color_cfg.morphology_enabled:
            morph_k = max(3, int(color_cfg.morphology_kernel_size) | 1)
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (morph_k, morph_k))
            edges_closed = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel)
        contours, _ = cv2.findContours(edges_closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        overlay = detection_image.copy()
        for contour in contours:
            perimeter = cv2.arcLength(contour, True)
            if perimeter <= 0:
                continue
            approx = cv2.approxPolyDP(contour, 0.02 * perimeter, True)
            color = (0, 255, 255) if len(approx) == 4 else (0, 165, 255)
            cv2.drawContours(overlay, [contour], -1, color, 2)
        return {"gray": gray, "blurred": blurred, "edges_closed": edges_closed, "contour_overlay": overlay}
    except Exception as exc:
        print(f"[ERROR] Edge visualization failed: {exc}")
        return None


def _draw_final_roi_bbox(raw_image: np.ndarray, roi_info: dict) -> np.ndarray:
    output = raw_image.copy()
    bbox = roi_info.get("bbox")
    if not bbox or len(bbox) != 4:
        return output
    x, y, w, h = map(int, bbox)
    color = (0, 0, 255) if roi_info.get("fallback_used") else (0, 255, 0)
    cv2.rectangle(output, (x, y), (x + w, y + h), color, 3, cv2.LINE_AA)
    label = f"{roi_info.get('method', '?')} score={float(roi_info.get('score', 0)):.3f}"
    cv2.putText(output, label, (x, max(20, y - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2, cv2.LINE_AA)
    return output


def _build_highlight_mask_visual(roi_image: np.ndarray, color_cfg) -> np.ndarray | None:
    try:
        h, w = roi_image.shape[:2]
        scale = float(color_cfg.clustering_size) / max(h, w)
        small = roi_image if scale >= 1 else cv2.resize(roi_image, (max(2, round(w * scale)), max(2, round(h * scale))), interpolation=cv2.INTER_AREA)
        lab = cv2.cvtColor(small, cv2.COLOR_BGR2LAB)
        if color_cfg.remove_highlights and color_cfg.use_lab:
            mask = lab[:, :, 0] < int(color_cfg.highlight_l_threshold)
        else:
            mask = np.ones(lab.shape[:2], dtype=bool)
        mask_visual = np.zeros_like(small)
        mask_visual[mask] = (0, 255, 0)
        mask_visual[~mask] = (0, 0, 255)
        return cv2.addWeighted(small, 0.6, mask_visual, 0.4, 0)
    except Exception as exc:
        print(f"[ERROR] Highlight visualization failed: {exc}")
        return None


def _rank_of(product_id: str | None, candidates) -> str:
    if product_id is None:
        return "N/A"
    match = next((c.rank for c in candidates if c.product_id == product_id), None)
    return str(match) if match is not None else "not in Top-K"


# ---------------------------------------------------------------------------
# Viewer
# ---------------------------------------------------------------------------

class ColorPluginViewer(KeyboardPagedViewer):
    def __init__(self, crops_data: list[dict]):
        super().__init__(item_count=len(crops_data), figsize=(20, 9), window_title="Color Plugin Viewer")
        self.crops_data = crops_data

    def render(self, fig, index: int) -> None:
        data = self.crops_data[index]
        axes = fig.subplots(2, 5)
        evidence = data["evidence"]
        info = _dominant_color_info(evidence.get("dominant_color"))

        fig.suptitle(
            f"[{index + 1}/{len(self.crops_data)}] Crop #{data['det_idx']} | Top1: {data['top1']} | "
            f"Rank Top-K: {data['rank_before']} -> {data['rank_after']}",
            fontsize=13, fontweight="bold",
        )

        panels = [
            (data["raw_crop"], "1. Raw crop"),
            (data["edge_visual"].get("gray") if data["edge_visual"] else None, "2. Gray"),
            (data["edge_visual"].get("blurred") if data["edge_visual"] else None, "3. Blurred"),
            (data["edge_visual"].get("edges_closed") if data["edge_visual"] else None, "4. Edges + morph"),
            (data["edge_visual"].get("contour_overlay") if data["edge_visual"] else None, "5. Rect candidates"),
            (data["roi_bbox_image"], "6. Final ROI"),
            (data["roi_crop"], "7. Cropped ROI"),
            (data["highlight_overlay"], "8. Highlight mask"),
        ]
        for ax, (img, title) in zip(axes.flat[:8], panels):
            if img is None:
                ax.text(0.5, 0.5, "N/A", ha="center", va="center")
            else:
                ax.imshow(to_rgb(img) if img.ndim == 3 else img, cmap=None if img.ndim == 3 else "gray")
            ax.set_title(title, fontsize=10)
            ax.axis("off")

        ax_dom = axes.flat[8]
        ax_dom.set_title("9. Color & Rank Info", fontsize=10, fontweight="bold")
        ax_dom.axis("off")
        ax_dom.set_xlim(0, 1)
        ax_dom.set_ylim(0, 1)
        if info is None:
            ax_dom.text(0.5, 0.5, "No dominant color", ha="center", va="center")
        else:
            ax_dom.add_patch(Rectangle((0.05, 0.15), 0.25, 0.7, facecolor=_hex_to_rgb01(info["hex"]), edgecolor="black", linewidth=2))
            L, a, b = info["lab"]
            lines = [
                f"HEX: {info['hex']}", f"RGB: {info['rgb']}", f"Lab: ({L:.1f}, {a:.1f}, {b:.1f})",
                f"Chroma: {info['chroma']:.1f}", f"Rank trước rerank: {data['rank_before']}", f"Rank sau rerank: {data['rank_after']}",
            ]
            for y_pos, text in zip((0.85, 0.70, 0.55, 0.40, 0.25, 0.10), lines):
                ax_dom.text(0.35, y_pos, text, fontsize=9, va="center")

        _draw_palette_bar(axes.flat[9], evidence.get("palette", []), evidence.get("percentages", []))
        fig.tight_layout()


def _collect_crops_data(pipeline, color_cfg, image_paths: list[Path], filter_ids: set[str] | None, top_k_display: int) -> list[dict]:
    crops_data: list[dict] = []
    color_plugin = _find_color_plugin(pipeline)

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
            if not crop_trace.plugin_result or "color" not in crop_trace.plugin_result.executed_plugins:
                continue
            candidates = crop_trace.retrieval_result.candidates
            if filter_ids and not any(str(c.product_id) in filter_ids for c in candidates[:top_k_display]):
                continue

            raw_crop = crop_trace.crop.raw_image_array
            if raw_crop is None or raw_crop.size == 0:
                continue

            top1 = str(crop_trace.retrieval_result.top_candidate.product_id) if crop_trace.retrieval_result.top_candidate else "None"
            evidence = crop_trace.plugin_result.evidence.get("color", {})
            if not evidence:
                continue

            rank_before = _rank_of(crop_trace.decision_result.product_id, candidates)
            rank_after = _rank_of(crop_trace.final_decision.product_id, candidates)

            edge_visual = _build_edge_visual(raw_crop, color_cfg, color_plugin) if color_cfg else None
            roi_info = evidence.get("roi", {})
            roi_bbox_image = _draw_final_roi_bbox(raw_crop, roi_info)
            roi_crop, highlight_overlay = None, None
            bbox = roi_info.get("bbox")
            if bbox and len(bbox) == 4:
                x, y, w, h = map(int, bbox)
                roi_crop = raw_crop[y : y + h, x : x + w]
                if roi_crop.size and color_cfg:
                    highlight_overlay = _build_highlight_mask_visual(roi_crop, color_cfg)

            crops_data.append({
                "det_idx": crop_trace.crop.detection_index, "top1": top1,
                "rank_before": rank_before, "rank_after": rank_after,
                "raw_crop": raw_crop, "edge_visual": edge_visual, "roi_bbox_image": roi_bbox_image,
                "roi_crop": roi_crop, "highlight_overlay": highlight_overlay, "evidence": evidence,
            })

    return crops_data


def main() -> None:
    parser = argparse.ArgumentParser(description="Debug giai đoạn Plugin Color trên pipeline thật.")
    parser.add_argument("--source", type=str, default=None, help="1 ảnh hoặc 1 thư mục ảnh (mặc định: data/query).")
    parser.add_argument("--filter-ids", type=str, default="", help='Chỉ hiện crop có SKU này trong Top-K, VD "5,6". Rỗng = không lọc.')
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

    print(f"plugins.color.enabled={cfg.plugins.color.enabled}  roi_enabled={cfg.plugins.color.roi_enabled}  "
          f"clustering_size={cfg.plugins.color.clustering_size}  n_clusters={cfg.plugins.color.n_clusters}")

    crops_data = _collect_crops_data(pipeline, cfg.plugins.color, image_paths, filter_ids, args.top_k_display)
    print(f"\n[INFO] {len(crops_data)} crop có chạy Color Plugin (trong {len(image_paths)} ảnh).")

    ColorPluginViewer(crops_data).show()


if __name__ == "__main__":
    main()
