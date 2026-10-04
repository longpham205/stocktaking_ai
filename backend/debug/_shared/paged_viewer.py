"""KeyboardPagedViewer — base class dùng chung cho mọi viewer matplotlib có
tính năng chuyển ảnh/crop bằng phím.

Trước đây tính năng này được cài đặt lại RIÊNG BIỆT ở 4 file khác nhau
(``debug_benchmark_labels.py``, ``debug_ocr.py``, ``debug_color.py``,
``debug_val_product_viewer.py``), mỗi nơi thiếu 1 vài phím khác nhau
(có nơi không có Esc, có nơi không có A/D, không nơi nào có Home/End hay
lưu ảnh). Class này gom lại 1 chỗ, đầy đủ phím, để mọi viewer kế thừa.

Cách dùng — kế thừa và chỉ cần cài đặt ``render(index)``::

    class MyViewer(KeyboardPagedViewer):
        def __init__(self, samples):
            super().__init__(item_count=len(samples), figsize=(14, 7))
            self.samples = samples

        def render(self, fig, index):
            sample = self.samples[index]
            ax = fig.add_subplot(1, 1, 1)
            ax.imshow(sample.image_rgb)
            ax.set_title(f"[{index + 1}/{len(self.samples)}] {sample.name}")

    MyViewer(samples).show()

Phím điều khiển (giống nhau ở MỌI viewer kế thừa class này):
    ←  / A      : ảnh trước
    →  / D      : ảnh sau
    Home        : ảnh đầu tiên
    End         : ảnh cuối cùng
    S           : lưu khung hình hiện tại ra PNG (save_dir)
    Esc / Q     : đóng cửa sổ
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt

_NEXT_KEYS = {"right", "d"}
_PREV_KEYS = {"left", "a"}
_QUIT_KEYS = {"escape", "q"}


class KeyboardPagedViewer:
    """Base class: quản lý index hiện tại + vòng lặp phím, không tự vẽ gì.

    Lớp con override ``render(fig, index)`` để vẽ nội dung của trang hiện
    tại lên ``fig`` (fig đã được ``clear()`` sẵn trước khi gọi).
    """

    def __init__(
        self,
        item_count: int,
        figsize: tuple[float, float] = (14, 8),
        start_index: int = 0,
        save_dir: str | Path | None = None,
        window_title: str = "Debug Viewer",
    ) -> None:
        self.item_count = item_count
        self.figsize = figsize
        self.index = start_index if item_count else 0
        self.save_dir = Path(save_dir) if save_dir else None
        self.window_title = window_title
        self.fig: plt.Figure | None = None

    # ------------------------------------------------------------------
    # Override trong lớp con
    # ------------------------------------------------------------------
    def render(self, fig: plt.Figure, index: int) -> None:
        """Vẽ nội dung trang `index` lên `fig`. Lớp con PHẢI override."""
        raise NotImplementedError

    def on_before_close(self) -> None:
        """Hook tuỳ chọn: chạy khi viewer sắp đóng (lớp con có thể override)."""

    # ------------------------------------------------------------------
    # Vòng đời viewer
    # ------------------------------------------------------------------
    def show(self) -> None:
        if self.item_count == 0:
            print("[INFO] Không có dữ liệu để hiển thị.")
            return

        self.fig = plt.figure(figsize=self.figsize)
        try:
            self.fig.canvas.manager.set_window_title(self.window_title)
        except Exception:
            pass  # không phải backend nào cũng hỗ trợ đặt tên cửa sổ

        self.fig.canvas.mpl_connect("key_press_event", self._on_key)
        self._redraw()
        self._print_controls()
        plt.show()

    def _print_controls(self) -> None:
        print("\nControls:")
        print("  <-/A : ảnh trước      ->/D : ảnh sau")
        print("  Home : ảnh đầu        End  : ảnh cuối")
        print("  S    : lưu ảnh hiện tại" + (f" -> {self.save_dir}" if self.save_dir else " (chưa cấu hình save_dir)"))
        print("  Esc/Q: thoát\n")

    def _redraw(self) -> None:
        assert self.fig is not None
        self.fig.clear()
        self.render(self.fig, self.index)
        self.fig.canvas.draw_idle()

    def _go_to(self, new_index: int) -> None:
        self.index = new_index % self.item_count
        self._redraw()

    def _save_current(self) -> None:
        if self.save_dir is None or self.fig is None:
            print("[WARN] save_dir chưa được cấu hình, bỏ qua lưu ảnh.")
            return
        self.save_dir.mkdir(parents=True, exist_ok=True)
        out_path = self.save_dir / f"frame_{self.index:04d}.png"
        self.fig.savefig(out_path, dpi=150, bbox_inches="tight")
        print(f"[SAVED] {out_path}")

    def _on_key(self, event) -> None:
        key = (event.key or "").lower()

        if key in _NEXT_KEYS:
            self._go_to(self.index + 1)
        elif key in _PREV_KEYS:
            self._go_to(self.index - 1)
        elif key == "home":
            self._go_to(0)
        elif key == "end":
            self._go_to(self.item_count - 1)
        elif key == "s":
            self._save_current()
        elif key in _QUIT_KEYS:
            self.on_before_close()
            plt.close(self.fig)
