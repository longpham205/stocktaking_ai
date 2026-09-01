"""Lightweight desktop user interface.

Responsibility: handle user input (file picker, threshold slider) and
trigger execution exclusively via `InferenceRunner` or `ValidationRunner`.
The UI must NEVER import or execute Detector, Retriever, or any AI model
class directly, and must NEVER bypass `InventoryPipeline` (see
02_MODULE_SPECIFICATION.md, Section 11).

Product identity flow (see src/catalog/metadata.py):

    product_id (stable internal numeric ID, string)
        -> data/metadata/product_ids.json
        -> gallery folder name
        -> gallery image

Since this is a stocktaking (inventory counting) system, the Inference
tab surfaces a prominent Total-count panel alongside a per-product
quantity breakdown, in addition to the flat item list.
"""

from __future__ import annotations

import json
import tkinter as tk
from collections import Counter
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import cv2
import numpy as np
from PIL import Image, ImageTk

from src.core.config import AppConfig, reload_config
from src.core.logger import get_logger
from src.core.utils import list_image_files
from src.inference.infer import InferenceRunner
from src.validation.validate import ValidationRunner

logger = get_logger(__name__)

_VIEWER_WIDTH = 900
_VIEWER_HEIGHT = 700
_VIEWER_MIN_WIDTH = 600
_VIEWER_MIN_HEIGHT = 450
_VIEWER_ZOOM_MIN = 0.10
_VIEWER_ZOOM_MAX = 5.00
_VIEWER_ZOOM_STEP = 0.25


class ImageViewer:
    """A single independent, zoomable/scrollable Toplevel image viewer."""

    def __init__(self, root: tk.Tk, title: str, offset: str) -> None:
        self._root = root
        self._title = title
        self._offset = offset
        self._window: tk.Toplevel | None = None
        self._canvas: tk.Canvas | None = None
        self._original_image: Image.Image | None = None
        self._photo: ImageTk.PhotoImage | None = None
        self._zoom = 1.0
        self._zoom_label: ttk.Label | None = None
        self._current_path: Path | None = None

    def show(self, image_path: Path) -> None:
        """Opens the viewer (or focuses/refreshes it if already open)."""
        self._current_path = image_path

        if self._window is not None:
            try:
                if self._window.winfo_exists():
                    self._load(image_path)
                    self._window.deiconify()
                    self._window.lift()
                    self._window.focus_force()
                    return
            except tk.TclError:
                pass

        self._create(image_path)

    def refresh(self) -> None:
        """Reloads the currently displayed image, if the viewer is open."""
        if self._window is not None and self._current_path is not None:
            self._load(self._current_path)

    def close(self) -> None:
        """Closes the viewer window, if open."""
        if self._window is not None:
            try:
                self._window.destroy()
            except tk.TclError:
                pass
        self._window = None
        self._canvas = None
        self._original_image = None
        self._photo = None
        self._zoom_label = None

    def _create(self, image_path: Path) -> None:
        """Builds the Toplevel window and its controls."""
        window = tk.Toplevel(self._root)
        self._window = window
        window.title(self._title)
        window.geometry(f"{_VIEWER_WIDTH}x{_VIEWER_HEIGHT}{self._offset}")
        window.minsize(_VIEWER_MIN_WIDTH, _VIEWER_MIN_HEIGHT)

        toolbar = ttk.Frame(window)
        toolbar.pack(fill=tk.X, padx=8, pady=8)
        ttk.Button(toolbar, text="-", width=4, command=self._zoom_out).pack(side=tk.LEFT)
        ttk.Button(toolbar, text="100%", command=self._zoom_100).pack(side=tk.LEFT, padx=4)
        ttk.Button(toolbar, text="+", width=4, command=self._zoom_in).pack(side=tk.LEFT)
        ttk.Button(toolbar, text="Fit", command=self._zoom_fit).pack(side=tk.LEFT, padx=4)
        self._zoom_label = ttk.Label(toolbar, text="100%")
        self._zoom_label.pack(side=tk.LEFT, padx=8)
        ttk.Button(toolbar, text="Close", command=self.close).pack(side=tk.RIGHT)

        container = ttk.Frame(window)
        container.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))
        canvas = tk.Canvas(container, background="black", highlightthickness=0)
        v_scroll = ttk.Scrollbar(container, orient=tk.VERTICAL, command=canvas.yview)
        h_scroll = ttk.Scrollbar(container, orient=tk.HORIZONTAL, command=canvas.xview)
        canvas.configure(xscrollcommand=h_scroll.set, yscrollcommand=v_scroll.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        v_scroll.grid(row=0, column=1, sticky="ns")
        h_scroll.grid(row=1, column=0, sticky="ew")
        container.rowconfigure(0, weight=1)
        container.columnconfigure(0, weight=1)
        self._canvas = canvas

        canvas.bind("<MouseWheel>", self._on_mousewheel)
        canvas.bind("<Button-4>", self._on_mousewheel_linux)
        canvas.bind("<Button-5>", self._on_mousewheel_linux)

        window.protocol("WM_DELETE_WINDOW", self.close)
        window.bind("<Escape>", lambda _event: self.close())

        self._load(image_path)
        window.focus_force()

    def _load(self, image_path: Path) -> None:
        """Loads a full-resolution image and renders it at the fit zoom level."""
        image = _load_pil_image(image_path)
        if image is None:
            return
        self._original_image = image
        self._zoom = self._calculate_fit_zoom(image)
        self._render()

    @staticmethod
    def _calculate_fit_zoom(image: Image.Image) -> float:
        """Computes the zoom level that fits the image within the viewport."""
        width, height = image.size
        if width <= 0 or height <= 0:
            return 1.0
        zoom = min(850 / width, 580 / height)
        return max(_VIEWER_ZOOM_MIN, min(zoom, _VIEWER_ZOOM_MAX))

    def _zoom_fit(self) -> None:
        if self._original_image is not None:
            self._zoom = self._calculate_fit_zoom(self._original_image)
            self._render()

    def _zoom_100(self) -> None:
        self._zoom = 1.0
        self._render()

    def _zoom_in(self) -> None:
        self._zoom = min(self._zoom + _VIEWER_ZOOM_STEP, _VIEWER_ZOOM_MAX)
        self._render()

    def _zoom_out(self) -> None:
        self._zoom = max(self._zoom - _VIEWER_ZOOM_STEP, _VIEWER_ZOOM_MIN)
        self._render()

    def _on_mousewheel(self, event: tk.Event) -> str:
        self._zoom_in() if event.delta > 0 else self._zoom_out()
        return "break"

    def _on_mousewheel_linux(self, event: tk.Event) -> str:
        if event.num == 4:
            self._zoom_in()
        elif event.num == 5:
            self._zoom_out()
        return "break"

    def _render(self) -> None:
        """Redraws the current image at the current zoom level."""
        if self._original_image is None or self._canvas is None:
            return
        try:
            width, height = self._original_image.size
            display_w = max(1, int(width * self._zoom))
            display_h = max(1, int(height * self._zoom))
            display_image = self._original_image.resize((display_w, display_h), Image.Resampling.LANCZOS)
            self._photo = ImageTk.PhotoImage(display_image)
            self._canvas.delete("all")
            self._canvas.create_image(0, 0, anchor=tk.NW, image=self._photo)
            self._canvas.configure(scrollregion=(0, 0, display_w, display_h))
            if self._zoom_label is not None:
                self._zoom_label.configure(text=f"{self._zoom * 100:.0f}%")
        except Exception:
            logger.exception("Failed to render image viewer.")


def _read_image(image_path: Path | str) -> np.ndarray | None:
    """Reads an image using Unicode-safe path handling for Windows compatibility."""
    path = Path(image_path)
    try:
        data = np.fromfile(str(path), dtype=np.uint8)
        if data.size == 0:
            logger.warning("Image file is empty: %s", path)
            return None
        return cv2.imdecode(data, cv2.IMREAD_COLOR)
    except OSError:
        logger.exception("Failed to read image file: %s", path)
        return None


def _load_pil_image(image_path: Path) -> Image.Image | None:
    """Loads a full-resolution image into a PIL Image (RGB, no thumbnailing)."""
    image_bgr = _read_image(image_path)
    if image_bgr is None:
        messagebox.showerror("Image error", f"Could not load image:\n{image_path}")
        return None
    try:
        return Image.fromarray(cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB))
    except Exception:
        logger.exception("Failed to convert image: %s", image_path)
        return None


class StocktakingApp:
    """Tkinter desktop application for Stocktaking AI."""

    def __init__(self, root: tk.Tk, config: AppConfig) -> None:
        self._root = root
        self._config = config
        self._root.title(f"{config.app.name} v{config.app.version} - Desktop Demo")
        self._root.geometry("1300x750")  # Mở rộng kích thước tổng thể

        self._inference_runner = InferenceRunner(config)
        self._validation_runner: ValidationRunner | None = None

        self._product_id_map = self._load_product_id_mapping()

        self._selected_image_path = tk.StringVar(value="No image selected")
        self._threshold_var = tk.DoubleVar(value=config.decision.similarity_threshold)
        self._status_var = tk.StringVar(value="Ready.")
        self._total_count_var = tk.StringVar(value="0")
        self._inference_time_var = tk.StringVar(value="0.0 ms")

        self._preview_image_ref: ImageTk.PhotoImage | None = None
        self._match_image_ref: ImageTk.PhotoImage | None = None
        
        # Đảm bảo ban đầu chưa có đường dẫn -> Không load ảnh kết quả cũ
        self._current_annotated_image_path: Path | None = None
        self._current_match_image_path: Path | None = None

        self._annotated_viewer = ImageViewer(root, "Annotated Result Viewer", "+40+60")
        self._gallery_viewer = ImageViewer(root, "Matched Gallery Product Viewer", "+980+60")
        self._chart_viewer = ImageViewer(root, "Validation Chart Viewer", "+40+60")
        self._validation_image_viewer = ImageViewer(root, "Validation Image Viewer", "+980+60")
        self._last_validation_images: list[Path] = []

        self._build_layout()
        self._root.protocol("WM_DELETE_WINDOW", self._on_main_window_close)

        logger.info("StocktakingApp UI initialized. Loaded %d product mapping(s).", len(self._product_id_map))

    def _load_product_id_mapping(self) -> dict[str, str]:
        path = self._config.resolve_path(self._config.paths.metadata_dir) / self._config.catalog.product_ids_filename
        if not path.is_file():
            logger.warning("Product ID mapping not found yet: %s", path)
            return {}
        try:
            with path.open("r", encoding="utf-8") as file_handle:
                data = json.load(file_handle)
            products = data.get("products", {})
            return {str(pid): str(folder) for pid, folder in products.items()}
        except (json.JSONDecodeError, OSError):
            logger.exception("Failed to load product_ids.json")
            return {}

    def _resolve_product_folder(self, product_id: str) -> str | None:
        folder = self._product_id_map.get(str(product_id))
        if folder is None:
            logger.warning("Product ID '%s' not present in product_ids.json.", product_id)
        return folder

    # --- Layout ---

    def _build_layout(self) -> None:
        notebook = ttk.Notebook(self._root)
        notebook.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        inference_tab = ttk.Frame(notebook)
        validation_tab = ttk.Frame(notebook)
        notebook.add(inference_tab, text="Inference")
        notebook.add(validation_tab, text="Validation")

        self._build_inference_tab(inference_tab)
        self._build_validation_tab(validation_tab)

        status_bar = ttk.Label(self._root, textvariable=self._status_var, relief=tk.SUNKEN, anchor=tk.W)
        status_bar.pack(fill=tk.X, side=tk.BOTTOM)

    def _build_inference_tab(self, parent: ttk.Frame) -> None:
        """Xây dựng giao diện tab Inference theo dạng Cột [1 | 2 | 3]."""
        controls = ttk.Frame(parent)
        controls.pack(fill=tk.X, padx=8, pady=8)
        ttk.Button(controls, text="Select Image...", command=self._on_select_image).pack(side=tk.LEFT)
        ttk.Label(controls, textvariable=self._selected_image_path).pack(side=tk.LEFT, padx=8)

        threshold_frame = ttk.Frame(parent)
        threshold_frame.pack(fill=tk.X, padx=8, pady=(0, 6))
        ttk.Label(threshold_frame, text="Similarity Threshold:").pack(side=tk.LEFT)
        ttk.Scale(
            threshold_frame,
            from_=0.0,
            to=1.0,
            orient=tk.HORIZONTAL,
            variable=self._threshold_var,
            command=self._on_threshold_change,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=8)
        ttk.Label(threshold_frame, textvariable=self._threshold_var).pack(side=tk.LEFT)
        ttk.Button(threshold_frame, text="Run Inference", command=self._on_run_inference).pack(side=tk.LEFT, padx=(12, 0))

        # Layout chính dạng 3 Cột
        grid = ttk.Frame(parent)
        grid.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        
        # Cấu hình tỉ lệ trọng số 3 cột (Cột giữa rộng nhất để hiển thị ảnh kết quả)
        grid.columnconfigure(0, weight=1)  # Cột 1: Thông tin
        grid.columnconfigure(1, weight=4)  # Cột 2: Ảnh kết quả
        grid.columnconfigure(2, weight=2)  # Cột 3: SP nhận diện
        grid.rowconfigure(0, weight=1)

        # ==========================================
        # CỘT 1: THỐNG KÊ VÀ TỔNG SẢN PHẨM
        # ==========================================
        col1_frame = ttk.Frame(grid)
        col1_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        col1_frame.rowconfigure(1, weight=1)
        col1_frame.columnconfigure(0, weight=1)

        # Cột 1 - Hàng trên: Tổng số lượng & Thời gian
        summary_box = ttk.LabelFrame(col1_frame, text="Tổng quan")
        summary_box.grid(row=0, column=0, sticky="ew", pady=(0, 4))

        ttk.Label(summary_box, text="TỔNG SỐ SẢN PHẨM", font=("TkDefaultFont", 9, "bold")).pack(anchor=tk.W, padx=8, pady=(4, 0))
        ttk.Label(summary_box, textvariable=self._total_count_var, font=("TkDefaultFont", 22, "bold"), foreground="#008000").pack(anchor=tk.W, padx=8)

        ttk.Separator(summary_box, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=8, pady=4)

        ttk.Label(summary_box, text="THỜI GIAN XỬ LÝ", font=("TkDefaultFont", 9, "bold")).pack(anchor=tk.W, padx=8)
        ttk.Label(summary_box, textvariable=self._inference_time_var, font=("TkDefaultFont", 16, "bold"), foreground="#0066cc").pack(anchor=tk.W, padx=8, pady=(0, 4))

        # Cột 1 - Hàng dưới: Thống kê chi tiết từng sản phẩm
        stats_box = ttk.LabelFrame(col1_frame, text="Thống kê sản phẩm")
        stats_box.grid(row=1, column=0, sticky="nsew", pady=(4, 0))
        stats_box.rowconfigure(0, weight=1)
        stats_box.columnconfigure(0, weight=1)

        self._breakdown_tree = ttk.Treeview(stats_box, columns=("product_id", "product_name", "quantity"), show="headings")
        self._breakdown_tree.heading("product_id", text="ID")
        self._breakdown_tree.heading("product_name", text="Sản phẩm")
        self._breakdown_tree.heading("quantity", text="SL")
        self._breakdown_tree.column("product_id", width=50, anchor=tk.CENTER)
        self._breakdown_tree.column("product_name", width=140, anchor=tk.W)
        self._breakdown_tree.column("quantity", width=40, anchor=tk.CENTER)

        breakdown_scroll = ttk.Scrollbar(stats_box, orient=tk.VERTICAL, command=self._breakdown_tree.yview)
        self._breakdown_tree.configure(yscrollcommand=breakdown_scroll.set)
        self._breakdown_tree.grid(row=0, column=0, sticky="nsew", padx=(4, 0), pady=4)
        breakdown_scroll.grid(row=0, column=1, sticky="ns", padx=(0, 4), pady=4)

        # ==========================================
        # CỘT 2: ÁNH KẾT QUẢ (Bounding Boxes)
        # ==========================================
        col2_frame = ttk.LabelFrame(grid, text="Ảnh kết quả - Bounding Boxes")
        col2_frame.grid(row=0, column=1, sticky="nsew", padx=4)
        col2_frame.rowconfigure(0, weight=1)
        col2_frame.columnconfigure(0, weight=1)

        self._preview_label = ttk.Label(col2_frame, text="Bấm 'Run Inference' để xem kết quả.", anchor=tk.CENTER, cursor="hand2")
        self._preview_label.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        self._preview_label.bind("<Button-1>", lambda _e: self._open_annotated_viewer())
        self._preview_label.bind("<Configure>", lambda _e: self._show_annotated_preview())

        # ==========================================
        # CỘT 3: SẢN PHẨM NHẬN DIỆN & DANH SÁCH
        # ==========================================
        col3_frame = ttk.Frame(grid)
        col3_frame.grid(row=0, column=2, sticky="nsew", padx=(4, 0))
        col3_frame.rowconfigure(1, weight=1)
        col3_frame.columnconfigure(0, weight=1)

        # Cột 3 - Hàng trên: Ảnh Sản phẩm nhận diện (Crop)
        match_box = ttk.LabelFrame(col3_frame, text="Ảnh sản phẩm nhận diện")
        match_box.grid(row=0, column=0, sticky="nsew", pady=(0, 4))
        match_box.rowconfigure(0, weight=1)
        match_box.columnconfigure(0, weight=1)

        self._match_image_label = ttk.Label(match_box, text="Chọn sản phẩm bên dưới để xem ảnh.", anchor=tk.CENTER, cursor="hand2")
        self._match_image_label.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        self._match_image_label.bind("<Button-1>", lambda _e: self._open_gallery_viewer())
        self._match_image_label.bind("<Configure>", lambda _e: self._render_current_match_image())

        # Cột 3 - Hàng dưới: Danh sách sản phẩm chi tiết
        results_box = ttk.LabelFrame(col3_frame, text="Danh sách sản phẩm nhận diện")
        results_box.grid(row=1, column=0, sticky="nsew", pady=(4, 0))
        results_box.rowconfigure(0, weight=1)
        results_box.columnconfigure(0, weight=1)

        self._results_tree = ttk.Treeview(results_box, columns=("product_id", "product_name", "confidence"), show="headings")
        self._results_tree.heading("product_id", text="ID")
        self._results_tree.heading("product_name", text="Sản phẩm")
        self._results_tree.heading("confidence", text="Conf")
        self._results_tree.column("product_id", width=50, anchor=tk.CENTER)
        self._results_tree.column("product_name", width=140, anchor=tk.W)
        self._results_tree.column("confidence", width=50, anchor=tk.CENTER)

        results_scroll = ttk.Scrollbar(results_box, orient=tk.VERTICAL, command=self._results_tree.yview)
        self._results_tree.configure(yscrollcommand=results_scroll.set)
        self._results_tree.grid(row=0, column=0, sticky="nsew", padx=(4, 0), pady=4)
        results_scroll.grid(row=0, column=1, sticky="ns", padx=(0, 4), pady=4)
        self._results_tree.bind("<<TreeviewSelect>>", self._on_tree_select)

    def _build_validation_tab(self, parent: ttk.Frame) -> None:
        controls = ttk.Frame(parent)
        controls.pack(fill=tk.X, padx=8, pady=8)
        self._benchmark_dir_var = tk.StringVar(
            value=str(self._config.resolve_path(self._config.paths.benchmark_dir))
        )
        ttk.Button(controls, text="Select Benchmark Dir...", command=self._on_select_benchmark_dir).pack(side=tk.LEFT)
        ttk.Label(controls, textvariable=self._benchmark_dir_var).pack(side=tk.LEFT, padx=8)
        ttk.Button(parent, text="Run Validation", command=self._on_run_validation).pack(padx=8, pady=4, anchor=tk.W)

        chart_row = ttk.Frame(parent)
        chart_row.pack(fill=tk.X, padx=8, pady=(0, 4))
        ttk.Button(chart_row, text="View Metrics Chart", command=self._show_validation_chart).pack(side=tk.LEFT)
        ttk.Button(chart_row, text="View Annotated Images", command=self._show_next_validation_image).pack(side=tk.LEFT, padx=8)

        self._summary_text = tk.Text(parent, height=28, wrap=tk.WORD)
        self._summary_text.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

    # --- File & Threshold ---

    def _on_select_image(self) -> None:
        path = filedialog.askopenfilename(
            title="Select a query image", filetypes=[("Image files", "*.jpg *.jpeg *.png *.bmp *.webp")]
        )
        if path:
            self._selected_image_path.set(path)
            self._status_var.set(f"Selected image: {path}")

    def _on_select_benchmark_dir(self) -> None:
        directory = filedialog.askdirectory(title="Select benchmark directory")
        if directory:
            self._benchmark_dir_var.set(directory)

    def _on_threshold_change(self, _value: str) -> None:
        self._status_var.set(f"Similarity threshold: {round(self._threshold_var.get(), 2):.2f} (applies on next run)")

    # --- Inference ---

    def _on_run_inference(self) -> None:
        path = self._selected_image_path.get()
        if not path or path == "No image selected":
            messagebox.showwarning("No image", "Please select a query image first.")
            return

        self._status_var.set("Running inference...")
        self._root.update_idletasks()

        try:
            result = self._inference_runner.run_single(
                path, similarity_threshold=round(self._threshold_var.get(), 2)
            )
        except (FileNotFoundError, ValueError) as exc:
            messagebox.showerror("Inference failed", str(exc))
            self._status_var.set("Inference failed.")
            return

        self._inference_time_var.set(f"{result.processing_time_ms:.1f} ms")
        self._populate_results(result)

        # Cập nhật đường dẫn ảnh mới nhất của phiên chạy hiện tại
        annotated_path = (
            self._config.resolve_path(self._config.paths.output_dir)
            / self._config.storage.annotated_image_filename
        )
        if annotated_path.is_file():
            self._current_annotated_image_path = annotated_path

        self._show_annotated_preview()
        self._annotated_viewer.refresh()

        self._status_var.set(
            f"Inference complete: {result.total_items} item(s) in {result.processing_time_ms:.1f} ms"
        )

    def _populate_results(self, result) -> None:
        self._total_count_var.set(str(result.total_items))

        for row in self._breakdown_tree.get_children():
            self._breakdown_tree.delete(row)
        counts = Counter((item.product_id, item.product_name) for item in result.items)
        for (product_id, product_name), quantity in sorted(counts.items(), key=lambda kv: kv[1], reverse=True):
            self._breakdown_tree.insert("", tk.END, values=(product_id, product_name, quantity))

        for row in self._results_tree.get_children():
            self._results_tree.delete(row)
        self._current_match_image_path = None
        self._clear_match_preview("Chọn sản phẩm bên dưới để xem ảnh.")
        self._gallery_viewer.close()

        for item in result.items:
            self._results_tree.insert(
                "", tk.END,
                values=(item.product_id, item.product_name, f"{item.final_confidence:.2f}"),
            )

    # --- Validation ---

    def _on_run_validation(self) -> None:
        benchmark_dir = self._benchmark_dir_var.get()
        self._status_var.set("Running validation...")
        self._root.update_idletasks()

        if self._validation_runner is None:
            self._validation_runner = ValidationRunner(self._config)

        try:
            report = self._validation_runner.run(benchmark_dir)
        except FileNotFoundError as exc:
            messagebox.showerror("Validation failed", str(exc))
            self._status_var.set("Validation failed.")
            return

        lines = self._format_validation_summary(report)
        self._summary_text.delete("1.0", tk.END)
        self._summary_text.insert(tk.END, "\n".join(lines))
        self._status_var.set("Validation complete.")

        images_dir = (
            self._config.resolve_path(self._config.paths.output_dir) / self._config.validation.annotated_images_dirname
        )
        self._last_validation_images = sorted(images_dir.glob("*.jpg")) if images_dir.is_dir() else []

    def _format_validation_summary(self, report: dict) -> list[str]:
        lines = [
            f"Input images     : {report['dataset']['total_images']}",
            f"IoU threshold    : {report['configuration']['iou_match_threshold']}",
            f"Retrieval Top-K  : {report['configuration']['top_k']}",
            "",
        ]

        for stage_name in (
            "detection", "cropping", "overlap", "segmentation",
            "retrieval", "decision", "plugins", "fusion", "end_to_end",
        ):
            stage_report = report.get(stage_name)
            lines.append(f"[{stage_name.upper()}]")
            if stage_report == "skipped_by_config":
                lines.append("  (skipped by config)")
            elif stage_name == "plugins" and isinstance(stage_report, dict):
                for plugin_name, plugin_stats in stage_report.items():
                    lines.append(f"  {plugin_name}: {self._format_kv(plugin_stats)}")
            elif isinstance(stage_report, dict):
                lines.append(f"  {self._format_kv(stage_report)}")
            else:
                lines.append(f"  {stage_report}")
            lines.append("")

        lines.append("[LATENCY BREAKDOWN, ms]")
        for stage, values in report.get("latency", {}).items():
            lines.append(f"  {stage}: mean={values['mean']}  min={values['min']}  max={values['max']}")
        lines.append("")
        lines.append(
            f"Annotated images & chart saved under: "
            f"{self._config.resolve_path(self._config.paths.output_dir)}"
        )
        return lines

    @staticmethod
    def _format_kv(data: dict) -> str:
        parts = [f"{key}={value}" for key, value in data.items() if not isinstance(value, (dict, list))]
        return " ".join(parts)

    def _show_validation_chart(self) -> None:
        chart_path = self._config.resolve_path(self._config.paths.output_dir) / self._config.validation.chart_filename
        if chart_path.is_file():
            self._chart_viewer.show(chart_path)
        else:
            messagebox.showinfo("No chart", "Run validation first to generate the metrics chart.")

    def _show_next_validation_image(self) -> None:
        if not self._last_validation_images:
            messagebox.showinfo("No images", "Run validation first to generate annotated images.")
            return
        self._validation_image_index = getattr(self, "_validation_image_index", -1) + 1
        self._validation_image_index %= len(self._last_validation_images)
        self._validation_image_viewer.show(self._last_validation_images[self._validation_image_index])

    # --- Previews and Viewers ---

    def _create_preview_image(self, image_path: Path, target_widget: ttk.Label) -> ImageTk.PhotoImage | None:
        image_bgr = _read_image(image_path)
        if image_bgr is None:
            return None

        width = target_widget.winfo_width()
        height = target_widget.winfo_height()

        max_w = max(width - 8, 100)
        max_h = max(height - 8, 100)

        try:
            image = Image.fromarray(cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB))
            image.thumbnail((max_w, max_h), Image.Resampling.LANCZOS)
            return ImageTk.PhotoImage(image)
        except Exception:
            logger.exception("Failed to create preview image: %s", image_path)
            return None

    def _show_annotated_preview(self) -> None:
        """Chỉ hiển thị ảnh nếu đã chạy Inference trong phiên hiện tại."""
        if self._current_annotated_image_path is None:
            self._preview_label.configure(image="", text="Bấm 'Run Inference' để xem kết quả.")
            return

        if not self._current_annotated_image_path.is_file():
            self._preview_label.configure(image="", text="Không tìm thấy ảnh kết quả.")
            return

        photo = self._create_preview_image(self._current_annotated_image_path, self._preview_label)
        if photo is None:
            self._preview_label.configure(image="", text="Không thể hiển thị ảnh kết quả.")
            return

        self._preview_image_ref = photo
        self._preview_label.configure(image=self._preview_image_ref, text="")

    def _render_current_match_image(self) -> None:
        if self._current_match_image_path is None:
            return

        photo = self._create_preview_image(self._current_match_image_path, self._match_image_label)
        if photo is None:
            self._clear_match_preview(f"Không thể hiển thị ảnh:\n{self._current_match_image_path.name}")
            return

        self._match_image_ref = photo
        self._match_image_label.configure(image=self._match_image_ref, text="")

    def _open_annotated_viewer(self) -> None:
        if self._current_annotated_image_path is not None:
            self._annotated_viewer.show(self._current_annotated_image_path)

    def _on_tree_select(self, _event: tk.Event) -> None:
        selected = self._results_tree.selection()
        if not selected:
            self._clear_match_preview("Chọn sản phẩm bên dưới để xem ảnh.")
            return

        row_values = self._results_tree.item(selected[0], "values")
        if not row_values:
            return
        product_id = row_values[0]

        folder_name = self._resolve_product_folder(product_id)
        if not folder_name:
            self._clear_match_preview(f"Product ID not found:\n{product_id}")
            return

        product_dir = self._config.resolve_path(self._config.paths.gallery_dir) / folder_name
        if not product_dir.is_dir():
            self._clear_match_preview(f"No gallery folder for:\n{folder_name}")
            return

        images = list_image_files(product_dir)
        if not images:
            self._clear_match_preview(f"No images found in:\n{folder_name}")
            return

        self._current_match_image_path = images[0]
        self._render_current_match_image()
        self._gallery_viewer.refresh()

    def _open_gallery_viewer(self) -> None:
        if self._current_match_image_path is not None:
            self._gallery_viewer.show(self._current_match_image_path)

    def _clear_match_preview(self, message: str) -> None:
        self._match_image_label.configure(image="", text=message)
        self._match_image_ref = None
        self._current_match_image_path = None

    # --- Shutdown ---

    def _on_main_window_close(self) -> None:
        self._annotated_viewer.close()
        self._gallery_viewer.close()
        self._chart_viewer.close()
        self._validation_image_viewer.close()
        self._root.destroy()


def launch_app(config_path: str | None = None) -> None:
    """Launches the Stocktaking AI desktop application."""
    config = reload_config(config_path)
    root = tk.Tk()
    StocktakingApp(root, config)
    root.mainloop()