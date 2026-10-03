"""09_plugin_barcode_viewer.py — Debug giai đoạn ⑦ PLUGIN: Barcode.

Khác với 07/08 (OCR/Color — chạy qua đúng OcrPlugin/ColorPlugin thật của
pipeline), file này CỐ Ý gọi thẳng ``pyzbar.decode()`` — 1 lớp thấp hơn
``BarcodePlugin`` (src/plugins/barcode.py, giải mã thích ứng 9 giai đoạn)
— để xem "bản thân pyzbar phát hiện được gì trên ảnh gốc, chưa qua bất kỳ
tiền xử lý nào (deskew/upscale/CLAHE/binarize)". Đây là lựa chọn có chủ
đích: nếu muốn debug đúng 9-stage decode thật của BarcodePlugin, cần viết
1 tool khác gọi trực tiếp `BarcodePlugin.run(crop)`.

So với check_barcode.py gốc, bản này:
    - Hết hardcode 1 ảnh + 1 path tuyệt đối máy cá nhân.
    - Chạy được trên cả thư mục, chuyển ảnh bằng phím (giống các viewer khác).
    - Vẫn giữ nguyên: gọi thẳng ``pyzbar.decode()``, không qua BarcodePlugin.

Cách dùng:
    python debug/09_plugin_barcode_viewer.py --source data/gallery/外箱ABA
    python debug/09_plugin_barcode_viewer.py --source path/to/1_anh.jpg

Controls: xem _shared/paged_viewer.py.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from matplotlib.patches import Rectangle
from pyzbar.pyzbar import decode as pyzbar_decode

from debug._shared.bootstrap import get_config, resolve_data_path
from debug._shared.io_utils import list_images, load_bgr, to_rgb


class BarcodeRawViewer:
    """Không kế thừa KeyboardPagedViewer vì cần chạy lại pyzbar mỗi lần
    chuyển ảnh (nhẹ, không cần cache) — nhưng dùng chung bộ phím chuẩn."""

    def __init__(self, image_paths: list[Path]):
        self.image_paths = image_paths
        self.index = 0

    def _decode(self, path: Path):
        image_bgr = load_bgr(path)
        return image_bgr, pyzbar_decode(image_bgr)

    def _render(self, fig):
        fig.clear()
        path = self.image_paths[self.index]
        image_bgr, decoded_objects = self._decode(path)

        ax = fig.add_subplot(1, 1, 1)
        ax.imshow(to_rgb(image_bgr))
        ax.axis("off")

        print(f"\n[{self.index + 1}/{len(self.image_paths)}] {path.name} -> {len(decoded_objects)} barcode(s)")

        if not decoded_objects:
            ax.text(0.02, 0.03, "NO BARCODE DETECTED", transform=ax.transAxes, fontsize=16, color="red",
                     fontweight="bold", bbox=dict(facecolor="white", alpha=0.8))
        for i, obj in enumerate(decoded_objects, start=1):
            barcode_type = obj.type
            barcode_data = obj.data.decode("utf-8", errors="replace")
            x, y, w, h = obj.rect.left, obj.rect.top, obj.rect.width, obj.rect.height
            print(f"  [BARCODE {i}] type={barcode_type} data={barcode_data!r} rect=({x},{y},{w},{h})")

            ax.add_patch(Rectangle((x, y), w, h, fill=False, linewidth=3, edgecolor="lime"))
            label = f"#{i} {barcode_type}\n{barcode_data}"
            ax.text(x, max(y - 10, 10), label, fontsize=11, color="white", va="bottom",
                    bbox=dict(facecolor="black", alpha=0.75, pad=5))

        ax.set_title(
            f"[{self.index + 1}/{len(self.image_paths)}] {path.name}\n"
            f"pyzbar (raw, không qua BarcodePlugin 9-stage) — {len(decoded_objects)} barcode(s)",
            fontsize=13, fontweight="bold",
        )
        fig.tight_layout()
        fig.canvas.draw_idle()

    def _on_key(self, event):
        key = (event.key or "").lower()
        if key in ("right", "d"):
            self.index = (self.index + 1) % len(self.image_paths)
        elif key in ("left", "a"):
            self.index = (self.index - 1) % len(self.image_paths)
        elif key == "home":
            self.index = 0
        elif key == "end":
            self.index = len(self.image_paths) - 1
        elif key in ("escape", "q"):
            import matplotlib.pyplot as plt
            plt.close(event.canvas.figure)
            return
        else:
            return
        self._render(event.canvas.figure)

    def show(self):
        import matplotlib.pyplot as plt

        if not self.image_paths:
            print("[INFO] Không có ảnh để hiển thị.")
            return
        fig = plt.figure(figsize=(14, 9))
        fig.canvas.mpl_connect("key_press_event", self._on_key)
        self._render(fig)
        print("\nControls: <-/A trước  ->/D sau  Home/End đầu-cuối  Esc/Q thoát\n")
        plt.show()


def main() -> None:
    parser = argparse.ArgumentParser(description="Debug pyzbar thô (raw, chưa qua BarcodePlugin 9-stage).")
    parser.add_argument("--source", type=str, default=None, help="1 ảnh hoặc 1 thư mục ảnh (mặc định: data/gallery, ảnh đầu tiên tìm thấy theo thư mục con).")
    parser.add_argument("--config", type=str, default=None)
    args = parser.parse_args()

    cfg = get_config(args.config)
    source = Path(args.source) if args.source else resolve_data_path(cfg, "gallery_dir")

    if source.is_file():
        image_paths = [source]
    elif source.is_dir():
        image_paths = list_images(source)
        if not image_paths:  # có thể source là gallery_dir gốc chứa nhiều thư mục con
            for sub in sorted(p for p in source.iterdir() if p.is_dir()):
                image_paths = list_images(sub)
                if image_paths:
                    break
    else:
        image_paths = []

    if not image_paths:
        print(f"[ERROR] Không tìm thấy ảnh nào trong: {source}")
        return

    print(f"[INFO] plugins.barcode.enabled (BarcodePlugin thật, KHÔNG áp dụng cho tool này) = {cfg.plugins.barcode.enabled}")
    BarcodeRawViewer(image_paths).show()


if __name__ == "__main__":
    main()
