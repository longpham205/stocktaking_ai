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

_PREVIEW_MAX_SIDE = 420
_MATCH_PREVIEW_SIZE = 420
_VIEWER_WIDTH = 900
_VIEWER_HEIGHT = 700
_VIEWER_MIN_WIDTH = 600
_VIEWER_MIN_HEIGHT = 450
_VIEWER_ZOOM_MIN = 0.10
_VIEWER_ZOOM_MAX = 5.00
_VIEWER_ZOOM_STEP = 0.25


class ImageViewer:
    """A single independent, zoomable/scrollable Toplevel image viewer.

    Used for both the "Annotated Result" and "Matched Gallery Product"
    viewers, so both can be open side-by-side simultaneously without
    duplicating the zoom/pan/scroll implementation twice.
    """

    def __init__(self, root: tk.Tk, title: str, offset: str) -> None:
        """Initializes (but does not yet show) the viewer.

        Args:
            root: The Tkinter root window this viewer belongs to.
            title: Window title.
            offset: Tk geometry offset string (e.g. "+40+60").
        """
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
        """Opens the viewer (or focuses/refreshes it if already open).

        Args:
            image_path: Full-resolution image file to display.
        """
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


def _read_image(image_path):
    """Reads an image using Unicode-safe path handling.

    cv2.imread can fail on Windows with non-ASCII paths; np.fromfile +
    cv2.imdecode avoids that problem.

    Args:
        image_path: Path to the image file.

    Returns:
        BGR image array, or None if the file could not be read/decoded.
    """
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


def _load_pil_image(image_path: Path):
    """Loads a full-resolution image into a PIL Image (RGB, no thumbnailing).

    Args:
        image_path: Path to the image file.

    Returns:
        A PIL Image, or None on failure.
    """
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
        """Initializes the desktop application.

        Args:
            root: The Tkinter root window.
            config: Fully validated application configuration.
        """
        self._root = root
        self._config = config
        self._root.title(f"{config.app.name} v{config.app.version} - Desktop Demo")
        self._root.geometry("1000x680")

        self._inference_runner = InferenceRunner(config)
        self._validation_runner: ValidationRunner | None = None

        self._product_id_map = self._load_product_id_mapping()

        self._selected_image_path = tk.StringVar(value="No image selected")
        self._threshold_var = tk.DoubleVar(value=config.decision.similarity_threshold)
        self._status_var = tk.StringVar(value="Ready.")
        self._total_count_var = tk.StringVar(value="0")

        self._preview_image_ref = None
        self._match_image_ref = None
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

    # ======================================================================
    # Product mapping
    # ======================================================================

    def _load_product_id_mapping(self) -> dict[str, str]:
        """Loads internal product_id -> gallery folder mapping.

        Returns:
            Mapping of product_id (string) to gallery folder name. Empty
            if `product_ids.json` has not been built yet (e.g. before the
            first `run.py` build step).
        """
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
        """Resolves a product_id to its gallery folder name, if known."""
        folder = self._product_id_map.get(str(product_id))
        if folder is None:
            logger.warning("Product ID '%s' not present in product_ids.json.", product_id)
        return folder

    # ======================================================================
    # Layout
    # ======================================================================

    def _build_layout(self) -> None:
        """Builds all Tkinter widgets."""
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
        """Builds the Inference tab using a 2x2 layout.

        Layout:
            ┌─────────────────────┬─────────────────────┐
            │                     │                     │
            │   Annotated Result  │   Matched Product   │
            │                     │                     │
            ├─────────────────────┼─────────────────────┤
            │                     │                     │
            │   Statistics        │   Recognition List  │
            │                     │                     │
            └─────────────────────┴─────────────────────┘
        """

        # ================================================================
        # TOP CONTROLS
        # ================================================================

        controls = ttk.Frame(parent)
        controls.pack(fill=tk.X, padx=8, pady=8)

        ttk.Button(
            controls,
            text="Select Image...",
            command=self._on_select_image,
        ).pack(side=tk.LEFT)

        ttk.Label(
            controls,
            textvariable=self._selected_image_path,
        ).pack(side=tk.LEFT, padx=8)

        # ================================================================
        # THRESHOLD
        # ================================================================

        threshold_frame = ttk.Frame(parent)
        threshold_frame.pack(fill=tk.X, padx=8, pady=(0, 6))

        ttk.Label(
            threshold_frame,
            text="Similarity Threshold:",
        ).pack(side=tk.LEFT)

        ttk.Scale(
            threshold_frame,
            from_=0.0,
            to=1.0,
            orient=tk.HORIZONTAL,
            variable=self._threshold_var,
            command=self._on_threshold_change,
        ).pack(
            side=tk.LEFT,
            fill=tk.X,
            expand=True,
            padx=8,
        )

        ttk.Label(
            threshold_frame,
            textvariable=self._threshold_var,
        ).pack(side=tk.LEFT)

        ttk.Button(
            threshold_frame,
            text="Run Inference",
            command=self._on_run_inference,
        ).pack(side=tk.LEFT, padx=(12, 0))

        # ================================================================
        # MAIN 2x2 GRID
        # ================================================================

        grid = ttk.Frame(parent)
        grid.pack(
            fill=tk.BOTH,
            expand=True,
            padx=8,
            pady=8,
        )

        # 2 columns
        grid.columnconfigure(0, weight=1, uniform="main_col")
        grid.columnconfigure(1, weight=1, uniform="main_col")

        # 2 rows
        grid.rowconfigure(0, weight=1, uniform="main_row")
        grid.rowconfigure(1, weight=1, uniform="main_row")

        # ================================================================
        # TOP-LEFT
        # ẢNH KẾT QUẢ
        # ================================================================

        annotated_frame = ttk.LabelFrame(
            grid,
            text="Ảnh kết quả - Bounding Boxes",
        )

        annotated_frame.grid(
            row=0,
            column=0,
            sticky="nsew",
            padx=(0, 4),
            pady=(0, 4),
        )

        annotated_frame.rowconfigure(0, weight=1)
        annotated_frame.columnconfigure(0, weight=1)

        self._preview_label = ttk.Label(
            annotated_frame,
            text="Ảnh kết quả sẽ xuất hiện ở đây.",
            anchor=tk.CENTER,
            cursor="hand2",
        )

        self._preview_label.grid(
            row=0,
            column=0,
            sticky="nsew",
            padx=8,
            pady=8,
        )

        self._preview_label.bind(
            "<Button-1>",
            lambda _e: self._open_annotated_viewer(),
        )

        self._preview_label.bind(
            "<Double-Button-1>",
            lambda _e: self._open_annotated_viewer(),
        )

        # ================================================================
        # TOP-RIGHT
        # ẢNH SẢN PHẨM NHẬN DIỆN
        # ================================================================

        match_frame = ttk.LabelFrame(
            grid,
            text="Ảnh sản phẩm nhận diện",
        )

        match_frame.grid(
            row=0,
            column=1,
            sticky="nsew",
            padx=(4, 0),
            pady=(0, 4),
        )

        match_frame.rowconfigure(0, weight=1)
        match_frame.columnconfigure(0, weight=1)

        self._match_image_label = ttk.Label(
            match_frame,
            text="Chọn sản phẩm trong danh sách để xem ảnh.",
            anchor=tk.CENTER,
            cursor="hand2",
        )

        self._match_image_label.grid(
            row=0,
            column=0,
            sticky="nsew",
            padx=8,
            pady=8,
        )

        self._match_image_label.bind(
            "<Button-1>",
            lambda _e: self._open_gallery_viewer(),
        )

        self._match_image_label.bind(
            "<Double-Button-1>",
            lambda _e: self._open_gallery_viewer(),
        )

        # ================================================================
        # BOTTOM-LEFT
        # THỐNG KÊ
        # ================================================================

        statistics_frame = ttk.LabelFrame(
            grid,
            text="Thống kê",
        )

        statistics_frame.grid(
            row=1,
            column=0,
            sticky="nsew",
            padx=(0, 4),
            pady=(4, 0),
        )

        statistics_frame.rowconfigure(2, weight=1)
        statistics_frame.columnconfigure(0, weight=1)

        # -------------------------------
        # Tổng số
        # -------------------------------

        total_container = ttk.Frame(statistics_frame)
        total_container.grid(
            row=0,
            column=0,
            sticky="ew",
            padx=12,
            pady=(10, 4),
        )

        ttk.Label(
            total_container,
            text="TỔNG SỐ SẢN PHẨM",
            font=("TkDefaultFont", 10, "bold"),
        ).pack(anchor=tk.W)

        ttk.Label(
            total_container,
            textvariable=self._total_count_var,
            font=("TkDefaultFont", 28, "bold"),
        ).pack(anchor=tk.W)

        # -------------------------------
        # Separator
        # -------------------------------

        ttk.Separator(
            statistics_frame,
            orient=tk.HORIZONTAL,
        ).grid(
            row=1,
            column=0,
            sticky="ew",
            padx=12,
            pady=4,
        )

        # -------------------------------
        # Số lượng từng loại
        # -------------------------------

        ttk.Label(
            statistics_frame,
            text="SỐ LƯỢNG THEO SẢN PHẨM",
            font=("TkDefaultFont", 10, "bold"),
        ).grid(
            row=2,
            column=0,
            sticky="nw",
            padx=12,
            pady=(6, 4),
        )

        breakdown_container = ttk.Frame(statistics_frame)
        breakdown_container.grid(
            row=3,
            column=0,
            sticky="nsew",
            padx=8,
            pady=(0, 8),
        )

        statistics_frame.rowconfigure(3, weight=1)

        breakdown_columns = (
            "product_id",
            "product_name",
            "quantity",
        )

        self._breakdown_tree = ttk.Treeview(
            breakdown_container,
            columns=breakdown_columns,
            show="headings",
        )

        self._breakdown_tree.heading(
            "product_id",
            text="ID",
        )

        self._breakdown_tree.heading(
            "product_name",
            text="Sản phẩm",
        )

        self._breakdown_tree.heading(
            "quantity",
            text="SL",
        )

        self._breakdown_tree.column(
            "product_id",
            width=70,
            anchor=tk.CENTER,
        )

        self._breakdown_tree.column(
            "product_name",
            width=260,
            anchor=tk.W,
        )

        self._breakdown_tree.column(
            "quantity",
            width=70,
            anchor=tk.CENTER,
        )

        breakdown_scrollbar = ttk.Scrollbar(
            breakdown_container,
            orient=tk.VERTICAL,
            command=self._breakdown_tree.yview,
        )

        self._breakdown_tree.configure(
            yscrollcommand=breakdown_scrollbar.set,
        )

        self._breakdown_tree.pack(
            side=tk.LEFT,
            fill=tk.BOTH,
            expand=True,
        )

        breakdown_scrollbar.pack(
            side=tk.RIGHT,
            fill=tk.Y,
        )

        # ================================================================
        # BOTTOM-RIGHT
        # DANH SÁCH SẢN PHẨM NHẬN DIỆN
        # ================================================================

        results_frame = ttk.LabelFrame(
            grid,
            text="Danh sách sản phẩm nhận diện",
        )

        results_frame.grid(
            row=1,
            column=1,
            sticky="nsew",
            padx=(4, 0),
            pady=(4, 0),
        )

        results_frame.rowconfigure(0, weight=1)
        results_frame.columnconfigure(0, weight=1)

        tree_container = ttk.Frame(results_frame)
        tree_container.grid(
            row=0,
            column=0,
            sticky="nsew",
            padx=8,
            pady=8,
        )

        tree_container.rowconfigure(0, weight=1)
        tree_container.columnconfigure(0, weight=1)

        item_columns = (
            "product_id",
            "product_name",
            "confidence",
            "status",
        )

        self._results_tree = ttk.Treeview(
            tree_container,
            columns=item_columns,
            show="headings",
        )

        self._results_tree.heading(
            "product_id",
            text="ID",
        )

        self._results_tree.heading(
            "product_name",
            text="Sản phẩm",
        )

        self._results_tree.heading(
            "confidence",
            text="Confidence",
        )

        self._results_tree.heading(
            "status",
            text="Status",
        )

        self._results_tree.column(
            "product_id",
            width=60,
            anchor=tk.CENTER,
        )

        self._results_tree.column(
            "product_name",
            width=220,
            anchor=tk.W,
        )

        self._results_tree.column(
            "confidence",
            width=90,
            anchor=tk.CENTER,
        )

        self._results_tree.column(
            "status",
            width=90,
            anchor=tk.CENTER,
        )

        scrollbar = ttk.Scrollbar(
            tree_container,
            orient=tk.VERTICAL,
            command=self._results_tree.yview,
        )

        self._results_tree.configure(
            yscrollcommand=scrollbar.set,
        )

        self._results_tree.grid(
            row=0,
            column=0,
            sticky="nsew",
        )

        scrollbar.grid(
            row=0,
            column=1,
            sticky="ns",
        )

        # Click sản phẩm -> hiện ảnh ở TOP-RIGHT
        self._results_tree.bind(
            "<<TreeviewSelect>>",
            self._on_tree_select,
        )
    def _build_validation_tab(self, parent: ttk.Frame) -> None:
        """Builds the Validation tab: benchmark directory picker and VAL summary."""
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
        ttk.Button(chart_row, text="View Annotated Images", command=self._show_next_validation_image).pack(
            side=tk.LEFT, padx=8
        )

        self._summary_text = tk.Text(parent, height=28, wrap=tk.WORD)
        self._summary_text.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

    # ======================================================================
    # File / directory selection
    # ======================================================================

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
        """Handles similarity threshold slider movement (applied on next run)."""
        self._status_var.set(f"Similarity threshold: {round(self._threshold_var.get(), 2):.2f} (applies on next run)")

    # ======================================================================
    # Inference
    # ======================================================================

    def _on_run_inference(self) -> None:
        """Executes inference on the selected image via InferenceRunner."""
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

        self._populate_results(result)
        self._show_annotated_preview()
        self._annotated_viewer.refresh()

        self._status_var.set(
            f"Inference complete: {result.total_items} item(s) in {result.processing_time_ms:.1f} ms"
        )

    def _populate_results(self, result) -> None:
        """Fills the Total panel, breakdown table, and item Treeview.

        Args:
            result: The InventoryResult produced by the InferenceRunner.
        """
        self._total_count_var.set(str(result.total_items))

        for row in self._breakdown_tree.get_children():
            self._breakdown_tree.delete(row)
        counts = Counter((item.product_id, item.product_name) for item in result.items)
        for (product_id, product_name), quantity in sorted(counts.items(), key=lambda kv: kv[1], reverse=True):
            self._breakdown_tree.insert("", tk.END, values=(product_id, product_name, quantity))

        for row in self._results_tree.get_children():
            self._results_tree.delete(row)
        self._current_match_image_path = None
        self._clear_match_preview("Select an item to view gallery match.")
        self._gallery_viewer.close()

        for item in result.items:
            self._results_tree.insert(
                "", tk.END,
                values=(item.product_id, item.product_name, f"{item.final_confidence:.2f}", item.status),
            )

    # ======================================================================
    # Validation
    # ======================================================================

    def _on_run_validation(self) -> None:
        """Runs validation via ValidationRunner and displays the VAL summary."""
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
        """Formats the full VAL report into readable summary lines.

        Args:
            report: The report dictionary produced by ValidationRunner.

        Returns:
            List of text lines for display in the Validation tab.
        """
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
        """Formats a flat dict as a compact single-line string, skipping nested containers."""
        parts = [f"{key}={value}" for key, value in data.items() if not isinstance(value, (dict, list))]
        return " ".join(parts)

    def _show_validation_chart(self) -> None:
        """Opens the per-stage precision/recall/F1 bar chart in an ImageViewer, if it exists."""
        chart_path = self._config.resolve_path(self._config.paths.output_dir) / self._config.validation.chart_filename
        if chart_path.is_file():
            self._chart_viewer.show(chart_path)
        else:
            messagebox.showinfo("No chart", "Run validation first to generate the metrics chart.")

    def _show_next_validation_image(self) -> None:
        """Cycles through color-coded annotated validation images, one per click."""
        if not self._last_validation_images:
            messagebox.showinfo("No images", "Run validation first to generate annotated images.")
            return
        self._validation_image_index = getattr(self, "_validation_image_index", -1) + 1
        self._validation_image_index %= len(self._last_validation_images)
        self._validation_image_viewer.show(self._last_validation_images[self._validation_image_index])

    # ======================================================================
    # Annotated preview
    # ======================================================================
    
    
    def _create_preview_image(
        self,
        image_path: Path,
        max_width: int = 520,
        max_height: int = 320,
    ):
        """Loads an image and creates a thumbnail suitable for the UI."""

        image_bgr = _read_image(image_path)

        if image_bgr is None:
            return None

        try:
            image = Image.fromarray(
                cv2.cvtColor(
                    image_bgr,
                    cv2.COLOR_BGR2RGB,
                )
            )

            image.thumbnail(
                (max_width, max_height),
                Image.Resampling.LANCZOS,
            )

            return ImageTk.PhotoImage(image)

        except Exception:
            logger.exception(
                "Failed to create preview image: %s",
                image_path,
            )
            return None

    def _show_annotated_preview(self) -> None:
        """Displays the annotated result image in the top-left panel."""

        annotated_path = (
            self._config.resolve_path(
                self._config.paths.output_dir
            )
            / self._config.storage.annotated_image_filename
        )

        if not annotated_path.is_file():
            self._current_annotated_image_path = None
            self._preview_label.configure(
                image="",
                text="Không tìm thấy ảnh kết quả.",
            )
            return

        self._current_annotated_image_path = annotated_path

        photo = self._create_preview_image(
            annotated_path,
            max_width=520,
            max_height=330,
        )

        if photo is None:
            self._preview_label.configure(
                image="",
                text="Không thể hiển thị ảnh kết quả.",
            )
            return

        self._preview_image_ref = photo

        self._preview_label.configure(
            image=self._preview_image_ref,
            text="",
        )

    def _open_annotated_viewer(self) -> None:
        if self._current_annotated_image_path is not None:
            self._annotated_viewer.show(self._current_annotated_image_path)

    # ======================================================================
    # Gallery match
    # ======================================================================

    def _on_tree_select(self, _event: tk.Event) -> None:
        """Displays the matched gallery image for the selected inventory item."""
        selected = self._results_tree.selection()
        if not selected:
            self._clear_match_preview("Select an item to view gallery match.")
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

        image_path = images[0]
        self._current_match_image_path = image_path
        image_bgr = _read_image(image_path)
        if image_bgr is None:
            self._clear_match_preview(f"Failed to load image:\n{image_path.name}")
            return

        try:
            pil_image = Image.fromarray(
                cv2.cvtColor(
                    image_bgr,
                    cv2.COLOR_BGR2RGB,
                )
            )

            pil_image.thumbnail(
                (520, 330),
                Image.Resampling.LANCZOS,
            )

            self._match_image_ref = ImageTk.PhotoImage(
                pil_image
            )

            self._match_image_label.configure(
                image=self._match_image_ref,
                text="",
            )

            # Nếu viewer đang mở thì refresh.
            # Nếu chưa mở thì không làm gì.
            self._gallery_viewer.refresh()

        except Exception:
            logger.exception(
                "Failed to display gallery image: %s",
                image_path,
            )

            self._clear_match_preview(
                f"Không thể hiển thị ảnh:\n{image_path.name}"
            )

    def _open_gallery_viewer(self) -> None:
        if self._current_match_image_path is not None:
            self._gallery_viewer.show(self._current_match_image_path)

    def _clear_match_preview(self, message: str) -> None:
        """Clears the gallery match thumbnail and shows a placeholder message."""
        self._match_image_label.configure(image="", text=message)
        self._match_image_ref = None
        self._current_match_image_path = None

    # ======================================================================
    # Shutdown
    # ======================================================================

    def _on_main_window_close(self) -> None:
        self._annotated_viewer.close()
        self._gallery_viewer.close()
        self._chart_viewer.close()
        self._validation_image_viewer.close()
        self._root.destroy()


def launch_app(config_path: str | None = None) -> None:
    """Launches the Stocktaking AI desktop application.

    Args:
        config_path: Optional explicit path to a config.yaml file.
    """
    config = reload_config(config_path)
    root = tk.Tk()
    StocktakingApp(root, config)
    root.mainloop()
