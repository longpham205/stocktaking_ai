"""
13_validation_dashboard.py — Debug/Validation (End-to-end)

VAL Product Viewer
------------------

Hiển thị kết quả của từng bước pipeline cho các product được chọn theo
GT label, dựa trên `records.csv` đã sinh sẵn từ `python run.py --mode
validate` (KHÔNG tự chạy pipeline — chỉ đọc lại kết quả VAL đã chạy).

Nguồn dữ liệu:

1. COCO annotation
   config.paths.benchmark_labels_dir (mặc định: data/benchmark/_annotations.coco.json)

2. Benchmark images
   config.paths.benchmark_images_dir (mặc định: data/benchmark/images/)

3. Detailed VAL CSV
   config.paths.output_dir / records.csv (mặc định: data/outputs/records.csv)

ĐÃ SỬA LỖI so với debug_val_product_viewer.py gốc: ``COCO_FILE`` trỏ vào
``data/benchmark/products.json`` — SAI file (đó là catalog SKU dạng
list, không phải COCO annotation dạng {images, categories, annotations}).
COCO thật nằm ở ``_annotations.coco.json``. Nay lấy path trực tiếp từ
``config.yaml`` (qua ``debug._shared.bootstrap``) để không lệch nữa nếu
cấu hình đổi. Cũng đổi ``cv2.imread`` (không đọc được path Unicode) sang
``load_bgr`` an toàn Unicode, dùng chung với mọi tool khác trong debug/.

Viewer:

    ┌────────────────────┬────────────────────┬────────────────────┐
    │ 1. ORIGINAL        │ 2. PRODUCT CROP    │ 3. RETRIEVAL       │
    │                    │                    │                    │
    ├────────────────────┼────────────────────┼────────────────────┤
    │ 4. PIPELINE TRACE  │ 5. PERFORMANCE     │ 6. FINAL DECISION  │
    │                    │                    │                    │
    └────────────────────┴────────────────────┴────────────────────┘


Controls:

    ← / →     Previous / Next product
    Home/End  Sample đầu / cuối
    S         Lưu ảnh hiện tại ra PNG
    ESC       Exit

Cách dùng:
    python run.py --mode validate --benchmark-dir data/benchmark   # 1 lần, để có records.csv
    python debug/13_validation_dashboard.py --labels 7
    python debug/13_validation_dashboard.py --labels 5,6,7,8 --max-per-label 10 --only-wrong
"""

import argparse
import sys
from pathlib import Path
import json

import cv2
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from debug._shared.bootstrap import get_config, resolve_data_path
from debug._shared.io_utils import load_bgr


# ============================================================
# CLI ARGS (override các hằng số CONFIG bên dưới nếu truyền vào)
# ============================================================

_parser = argparse.ArgumentParser(description="VAL Product Viewer — dashboard 6 ô theo từng GT label.")
_parser.add_argument("--labels", type=str, default="7", help='category_id (GT) cần xem, cách nhau dấu phẩy, VD "5,6,7,8".')
_parser.add_argument("--max-per-label", type=int, default=None)
_parser.add_argument("--only-wrong", action="store_true", default=None, help="Chỉ xem sample có correct=False. Không truyền = giữ mặc định ONLY_WRONG bên dưới.")
_parser.add_argument("--config", type=str, default=None)
_args = _parser.parse_args()

_cfg = get_config(_args.config)


# ============================================================
# ROOT
# ============================================================

ROOT_DIR = Path(__file__).resolve().parents[1]


# ============================================================
# PATHS (lấy từ config.yaml — sửa lỗi trỏ nhầm products.json ở bản gốc)
# ============================================================

IMAGES_DIR = resolve_data_path(_cfg, "benchmark_images_dir")

COCO_FILE = resolve_data_path(_cfg, "benchmark_labels_dir")

VAL_CSV = resolve_data_path(_cfg, "output_dir") / _cfg.validation.records_filename


# ============================================================
# LABEL CONFIG (mặc định lấy từ --labels/--max-per-label/--only-wrong,
# có thể sửa tay ở đây nếu muốn hardcode lại như bản gốc)
# ============================================================

# GT label muốn xem
#
# Ví dụ:
# TARGET_LABELS = [5]
#
# hoặc:
# TARGET_LABELS = [5, 6, 7, 8]

TARGET_LABELS = [int(x) for x in _args.labels.split(",") if x.strip()]

# None = xem tất cả
#
# Ví dụ:
# MAX_PER_LABEL = 10
#
# => mỗi label chỉ xem tối đa 10 sample

MAX_PER_LABEL = _args.max_per_label

# True:
#   Chỉ xem những product bị nhận diện sai.
#
# False:
#   Xem tất cả product thuộc TARGET_LABELS.
#
# "Sai" được xác định bởi cột:
#     correct == False
#
ONLY_WRONG = True if _args.only_wrong is None else _args.only_wrong


# ============================================================
# WINDOW CONFIG
# ============================================================

# Kích thước figure
#
# width, height
#
# Đây là đơn vị inch.

FIGSIZE = (14, 10)

# DPI
DPI = 100

# Tự maximize cửa sổ trên Windows
AUTO_MAXIMIZE = False


# ============================================================
# IMAGE CONFIG
# ============================================================

# Padding khi crop GT
CROP_PADDING = 0

# Hiển thị GT bbox trên ảnh Original
SHOW_BBOX = True


# ------------------------------------------------------------
# ORIGINAL IMAGE DISPLAY SIZE
# ------------------------------------------------------------

# Ảnh Original sẽ được resize CHỈ để hiển thị.
#
# Không ảnh hưởng dữ liệu gốc.
#
# None = không giới hạn.

DISPLAY_ORIGINAL_MAX_WIDTH = 1000
DISPLAY_ORIGINAL_MAX_HEIGHT = 650


# ------------------------------------------------------------
# CROP IMAGE DISPLAY SIZE
# ------------------------------------------------------------

# GT crop cũng được resize CHỈ để hiển thị.

DISPLAY_CROP_MAX_WIDTH = 650
DISPLAY_CROP_MAX_HEIGHT = 600


# ------------------------------------------------------------
# IMAGE INTERPOLATION
# ------------------------------------------------------------

# Các lựa chọn:
#
# "nearest"
# "bilinear"
# "bicubic"
#
# nearest phù hợp khi muốn giữ pixel rõ.
# bilinear thường nhìn mượt hơn.

IMAGE_INTERPOLATION = "bilinear"


# ============================================================
# PANEL LAYOUT CONFIG
# ============================================================

# Khoảng cách ngang giữa các panel.
PANEL_WSPACE = 0.25

# Khoảng cách dọc giữa các panel.
PANEL_HSPACE = 0.35


# Tỷ lệ chiều cao:
#
# Hàng trên:
#   Original / Crop / Retrieval
#
# Hàng dưới:
#   Pipeline / Performance / Final
#
# Hàng dưới cao hơn vì có nhiều text.

PANEL_HEIGHT_RATIOS = (
    1.0,
    1.15,
)


# ============================================================
# FONT CONFIG
# ============================================================

# Tiêu đề chính
FONT_TITLE = 16

# Tiêu đề từng panel
FONT_SUBTITLE = 12

# Nội dung thông tin
FONT_INFO = 10

# Text nhỏ
FONT_SMALL = 8

# Footer
FONT_FOOTER = 10


# ============================================================
# TEXT CONFIG
# ============================================================

# Vị trí text trong panel
TEXT_X = 0.04
TEXT_Y = 0.92

# Padding title
TITLE_PAD = 10


# ============================================================
# HELPERS
# ============================================================

def load_coco():

    with open(
        COCO_FILE,
        "r",
        encoding="utf-8"
    ) as f:

        coco = json.load(f)

    images = {
        item["id"]: item
        for item in coco["images"]
    }

    categories = {
        item["id"]: item
        for item in coco["categories"]
    }

    annotations = coco["annotations"]

    return (
        images,
        categories,
        annotations
    )


# ------------------------------------------------------------

def find_image(file_name):

    path = IMAGES_DIR / Path(file_name).name

    if path.exists():
        return path

    matches = list(
        IMAGES_DIR.rglob(
            Path(file_name).name
        )
    )

    if matches:
        return matches[0]

    return None


# ------------------------------------------------------------

def crop_bbox(
    image,
    bbox,
    padding=0
):

    h, w = image.shape[:2]

    x, y, bw, bh = bbox

    x1 = max(
        0,
        int(x - padding)
    )

    y1 = max(
        0,
        int(y - padding)
    )

    x2 = min(
        w,
        int(x + bw + padding)
    )

    y2 = min(
        h,
        int(y + bh + padding)
    )

    if x2 <= x1 or y2 <= y1:
        return None

    return image[
        y1:y2,
        x1:x2
    ]


# ------------------------------------------------------------

def resize_for_display(
    image,
    max_width=None,
    max_height=None
):
    """
    Resize ảnh CHỈ phục vụ hiển thị.

    Không thay đổi ảnh gốc.

    Giữ nguyên aspect ratio.

    Returns
    -------
    resized_image, scale_x, scale_y
    """

    if image is None:
        return None, 1.0, 1.0

    h, w = image.shape[:2]

    scale = 1.0

    if (
        max_width is not None
        and w > max_width
    ):

        scale = min(
            scale,
            max_width / w
        )

    if (
        max_height is not None
        and h > max_height
    ):

        scale = min(
            scale,
            max_height / h
        )

    if scale >= 1.0:

        return (
            image,
            1.0,
            1.0
        )

    new_width = max(
        1,
        int(round(w * scale))
    )

    new_height = max(
        1,
        int(round(h * scale))
    )

    resized = cv2.resize(
        image,
        (
            new_width,
            new_height
        ),
        interpolation=cv2.INTER_AREA
    )

    scale_x = new_width / w
    scale_y = new_height / h

    return (
        resized,
        scale_x,
        scale_y
    )


# ------------------------------------------------------------

def safe_value(
    value,
    default=""
):
    """
    Chuyển NaN / None thành giá trị dễ hiển thị.
    """

    if pd.isna(value):
        return default

    return value


# ------------------------------------------------------------

def safe_float(
    value,
    default=0.0
):

    try:

        if pd.isna(value):
            return default

        return float(value)

    except Exception:

        return default


# ------------------------------------------------------------

def safe_int(
    value,
    default=None
):

    try:

        if pd.isna(value):
            return default

        return int(float(value))

    except Exception:

        return default


# ------------------------------------------------------------

def format_number(
    value,
    digits=4
):

    try:

        if pd.isna(value):
            return "N/A"

        return f"{float(value):.{digits}f}"

    except Exception:

        return str(value)


# ------------------------------------------------------------

def parse_bool(value):

    if isinstance(
        value,
        bool
    ):

        return value

    if pd.isna(value):

        return False

    return (
        str(value)
        .strip()
        .lower()
        in {
            "true",
            "1",
            "yes",
            "y",
        }
    )


# ============================================================
# BUILD GT LOOKUP
# ============================================================

def build_gt_lookup(
    images,
    annotations
):

    """
    Lookup:

        (image_id, category_id)
             ↓
        annotation list

    Một image có thể có nhiều object
    cùng category.
    """

    lookup = {}

    for ann in annotations:

        key = (
            ann["image_id"],
            ann["category_id"]
        )

        lookup.setdefault(
            key,
            []
        ).append(ann)

    return lookup


# ============================================================
# MATCH CSV ROW → COCO ANNOTATION
# ============================================================

def get_gt_annotation(
    row,
    images,
    annotations
):

    """
    Match CSV row với COCO annotation.

    Logic hiện tại:

        source_path
             ↓
        image filename
             ↓
        image_id
             ↓
        gt_product_id/category_id
             ↓
        candidates
             ↓
        detection_index

    Nếu không match được thì trả None.
    """

    # --------------------------------------------------------
    # Category
    # --------------------------------------------------------

    category_id = safe_int(
        row["gt_product_id"]
    )

    if category_id is None:
        return None

    # --------------------------------------------------------
    # Image filename
    # --------------------------------------------------------

    source_path = str(
        row["source_path"]
    )

    file_name = Path(
        source_path
    ).name

    # --------------------------------------------------------
    # Find image id
    # --------------------------------------------------------

    image_id = None

    for iid, info in images.items():

        if Path(
            info["file_name"]
        ).name == file_name:

            image_id = iid
            break

    if image_id is None:
        return None

    # --------------------------------------------------------
    # Find annotations
    # --------------------------------------------------------

    candidates = [
        ann
        for ann in annotations
        if (
            ann["image_id"] == image_id
            and ann["category_id"] == category_id
        )
    ]

    if not candidates:
        return None

    # --------------------------------------------------------
    # Detection index
    # --------------------------------------------------------

    detection_index = safe_int(
        row["detection_index"],
        default=0
    )

    if detection_index is None:
        detection_index = 0

    # --------------------------------------------------------
    # Match
    # --------------------------------------------------------

    if (
        0 <= detection_index
        < len(candidates)
    ):

        return candidates[
            detection_index
        ]

    # Fallback
    return candidates[0]


# ============================================================
# LOAD DATA
# ============================================================

def build_samples():

    print("Loading COCO...")

    (
        images,
        categories,
        annotations
    ) = load_coco()

    print(
        f"  Images      : {len(images)}"
    )

    print(
        f"  Categories  : {len(categories)}"
    )

    print(
        f"  Annotations : {len(annotations)}"
    )

    print("\nLoading VAL CSV...")

    df = pd.read_csv(
        VAL_CSV
    )

    print(
        f"  CSV rows    : {len(df)}"
    )

    # ========================================================
    # VALIDATE COLUMNS
    # ========================================================

    required = [
        "image_key",
        "source_path",
        "detection_index",
        "crop_id",
        "gt_product_id",
        "gt_matched",
        "detection_confidence",
        "detection_iou_gt",
        "overlap_flagged",
        "refinement_triggered",
        "refinement_used_fallback",
        "iou_before_refine",
        "iou_after_refine",
        "retrieval_top1_product_id",
        "retrieval_top1_similarity",
        "retrieval_gt_rank",
        "decision_status_before",
        "decision_trigger_reasons",
        "plugins_executed",
        "final_product_id",
        "final_status",
        "final_confidence",
        "changed_by_plugin",
        "detection_latency_ms",
        "retrieval_latency_ms",
        "plugin_latency_ms",
        "rerank_latency_ms",
        "correct",
    ]

    missing = [
        col
        for col in required
        if col not in df.columns
    ]

    if missing:

        raise ValueError(
            "CSV thiếu column:\n\n"
            + "\n".join(
                f"  - {col}"
                for col in missing
            )
        )

    # ========================================================
    # FILTER VALID GT
    # ========================================================

    df = df[
        df["gt_product_id"].notna()
    ].copy()

    df["gt_product_id"] = pd.to_numeric(
        df["gt_product_id"],
        errors="coerce"
    )

    df = df[
        df["gt_product_id"].notna()
    ].copy()

    df["gt_product_id"] = (
        df["gt_product_id"]
        .astype(int)
    )

    # ========================================================
    # FILTER TARGET LABELS
    # ========================================================

    df = df[
        df["gt_product_id"].isin(
            TARGET_LABELS
        )
    ].copy()


    # ========================================================
    # FILTER WRONG ONLY
    # ========================================================

    if ONLY_WRONG:

        # Chuẩn hóa cột correct về bool
        df["_correct_bool"] = (
            df["correct"]
            .apply(parse_bool)
        )

        # Chỉ giữ sample bị nhận diện sai
        df = df[
            ~df["_correct_bool"]
        ].copy()

        # Không cần dùng column phụ nữa
        df.drop(
            columns=["_correct_bool"],
            inplace=True
        )


    # ========================================================
    # LIMIT PER LABEL
    # ========================================================

    if MAX_PER_LABEL is not None:

        df = (
            df
            .groupby(
                "gt_product_id",
                group_keys=False
            )
            .head(
                MAX_PER_LABEL
            )
        )

    # ========================================================
    # BUILD SAMPLES
    # ========================================================

    samples = []

    for _, row in df.iterrows():

        gt_annotation = get_gt_annotation(
            row,
            images,
            annotations
        )

        samples.append({
            "row": row,
            "gt_annotation": gt_annotation,
            "categories": categories,
        })

    return samples


# ============================================================
# VIEWER
# ============================================================

class VALViewer:

    def __init__(
        self,
        samples
    ):

        self.samples = samples

        self.index = 0

        self.fig = plt.figure(
            figsize=FIGSIZE,
            dpi=DPI
        )

    # ========================================================
    # SHOW
    # ========================================================

    def show(self):

        self.fig.canvas.mpl_connect(
            "key_press_event",
            self.on_key
        )

        # ----------------------------------------------------
        # Maximize Windows
        # ----------------------------------------------------

        if AUTO_MAXIMIZE:

            manager = (
                plt.get_current_fig_manager()
            )

            try:

                manager.window.state(
                    "zoomed"
                )

            except Exception:

                try:

                    manager.window.showMaximized()

                except Exception:

                    pass

        # ----------------------------------------------------
        # Initial display
        # ----------------------------------------------------

        self.update()

        plt.show()

    # ========================================================
    # UPDATE
    # ========================================================

    def update(self):

        self.fig.clear()

        sample = self.samples[
            self.index
        ]

        row = sample["row"]

        annotation = sample[
            "gt_annotation"
        ]

        # ====================================================
        # LOAD IMAGE
        # ====================================================

        image_path = find_image(
            row["source_path"]
        )

        if image_path is None:

            print(
                "Không tìm thấy:",
                row["source_path"]
            )

            return

        try:
            image = load_bgr(
                image_path
            )
        except (FileNotFoundError, ValueError) as exc:

            print(
                "Không đọc được:",
                image_path,
                f"({exc})",
            )

            return

        image_rgb = cv2.cvtColor(
            image,
            cv2.COLOR_BGR2RGB
        )

        # ====================================================
        # RESIZE ORIGINAL FOR DISPLAY
        # ====================================================

        (
            image_display,
            image_scale_x,
            image_scale_y
        ) = resize_for_display(
            image_rgb,
            DISPLAY_ORIGINAL_MAX_WIDTH,
            DISPLAY_ORIGINAL_MAX_HEIGHT
        )

        # ====================================================
        # GT CROP
        # ====================================================

        crop_display = None

        if annotation is not None:

            crop = crop_bbox(
                image,
                annotation["bbox"],
                CROP_PADDING
            )

            if crop is not None:

                crop_rgb = cv2.cvtColor(
                    crop,
                    cv2.COLOR_BGR2RGB
                )

                (
                    crop_display,
                    _,
                    _
                ) = resize_for_display(
                    crop_rgb,
                    DISPLAY_CROP_MAX_WIDTH,
                    DISPLAY_CROP_MAX_HEIGHT
                )

        # ====================================================
        # GRID LAYOUT
        # ====================================================

        grid = self.fig.add_gridspec(
            2,
            3,
            height_ratios=PANEL_HEIGHT_RATIOS,
            hspace=PANEL_HSPACE,
            wspace=PANEL_WSPACE,
        )

        ax_original = self.fig.add_subplot(
            grid[0, 0]
        )

        ax_crop = self.fig.add_subplot(
            grid[0, 1]
        )

        ax_retrieval = self.fig.add_subplot(
            grid[0, 2]
        )

        ax_pipeline = self.fig.add_subplot(
            grid[1, 0]
        )

        ax_timing = self.fig.add_subplot(
            grid[1, 1]
        )

        ax_final = self.fig.add_subplot(
            grid[1, 2]
        )

        # ====================================================
        # 1. ORIGINAL
        # ====================================================

        ax_original.imshow(
            image_display,
            interpolation=IMAGE_INTERPOLATION
        )

        ax_original.set_title(
            "1. ORIGINAL / DETECTION",
            fontsize=FONT_SUBTITLE,
            pad=TITLE_PAD
        )

        ax_original.axis("off")

        # ----------------------------------------------------
        # BBOX
        # ----------------------------------------------------

        if (
            SHOW_BBOX
            and annotation is not None
        ):

            x, y, w, h = (
                annotation["bbox"]
            )

            # Scale bbox theo ảnh display
            x_display = (
                x * image_scale_x
            )

            y_display = (
                y * image_scale_y
            )

            w_display = (
                w * image_scale_x
            )

            h_display = (
                h * image_scale_y
            )

            rect = Rectangle(
                (
                    x_display,
                    y_display
                ),
                w_display,
                h_display,
                fill=False,
                linewidth=2
            )

            ax_original.add_patch(
                rect
            )

            ax_original.text(
                x_display,
                max(
                    0,
                    y_display - 5
                ),
                f"GT {row['gt_product_id']}",
                fontsize=FONT_SMALL,
                backgroundcolor="white"
            )

        # ====================================================
        # 2. PRODUCT CROP
        # ====================================================

        if crop_display is not None:

            ax_crop.imshow(
                crop_display,
                interpolation=IMAGE_INTERPOLATION
            )

        else:

            ax_crop.text(
                0.5,
                0.5,
                "GT crop unavailable",
                ha="center",
                va="center",
                fontsize=FONT_INFO,
                transform=ax_crop.transAxes
            )

        ax_crop.set_title(
            "2. PRODUCT CROP",
            fontsize=FONT_SUBTITLE,
            pad=TITLE_PAD
        )

        ax_crop.axis("off")

        # ====================================================
        # 3. RETRIEVAL
        # ====================================================

        retrieval_top1 = safe_value(
            row["retrieval_top1_product_id"],
            "N/A"
        )

        similarity = format_number(
            row["retrieval_top1_similarity"],
            4
        )

        gt_rank = safe_value(
            row["retrieval_gt_rank"],
            "N/A"
        )

        gt_product = safe_value(
            row["gt_product_id"],
            "N/A"
        )

        try:

            retrieval_correct = (
                int(float(retrieval_top1))
                == int(float(gt_product))
            )

        except Exception:

            retrieval_correct = False

        retrieval_symbol = (
            "✓"
            if retrieval_correct
            else "✗"
        )

        retrieval_text = f"""
RETRIEVAL

GT Product
    {gt_product}

Top-1
    {retrieval_top1} {retrieval_symbol}

Similarity
    {similarity}

GT Rank
    {gt_rank}

────────────────

Latency
    {format_number(
        row["retrieval_latency_ms"],
        2
    )} ms
"""

        ax_retrieval.text(
            TEXT_X,
            TEXT_Y,
            retrieval_text,
            va="top",
            fontsize=FONT_INFO,
            family="monospace",
            transform=ax_retrieval.transAxes
        )

        ax_retrieval.set_title(
            "3. RETRIEVAL",
            fontsize=FONT_SUBTITLE,
            pad=TITLE_PAD
        )

        ax_retrieval.axis("off")

        # ====================================================
        # 4. PIPELINE TRACE
        # ====================================================

        pipeline_text = f"""
PIPELINE TRACE

Detection
─────────
Confidence : {format_number(
    row["detection_confidence"],
    4
)}
IoU GT     : {format_number(
    row["detection_iou_gt"],
    4
)}

Overlap
───────
Flagged    : {safe_value(
    row["overlap_flagged"],
    "N/A"
)}

Refinement
──────────
Triggered  : {safe_value(
    row["refinement_triggered"],
    "N/A"
)}
Fallback   : {safe_value(
    row["refinement_used_fallback"],
    "N/A"
)}

IoU before : {format_number(
    row["iou_before_refine"],
    4
)}
IoU after  : {format_number(
    row["iou_after_refine"],
    4
)}

Decision
────────
Before     : {safe_value(
    row["decision_status_before"],
    "N/A"
)}
Reason     : {safe_value(
    row["decision_trigger_reasons"],
    "N/A"
)}

Plugins
───────
{safe_value(
    row["plugins_executed"],
    "None"
)}

Changed
───────
{safe_value(
    row["changed_by_plugin"],
    "False"
)}
"""

        ax_pipeline.text(
            TEXT_X,
            TEXT_Y,
            pipeline_text,
            va="top",
            fontsize=FONT_INFO,
            family="monospace",
            transform=ax_pipeline.transAxes
        )

        ax_pipeline.set_title(
            "4. PIPELINE TRACE",
            fontsize=FONT_SUBTITLE,
            pad=TITLE_PAD
        )

        ax_pipeline.axis("off")

        # ====================================================
        # 5. PERFORMANCE
        # ====================================================

        detection_latency = safe_float(
            row["detection_latency_ms"]
        )

        retrieval_latency = safe_float(
            row["retrieval_latency_ms"]
        )

        plugin_latency = safe_float(
            row["plugin_latency_ms"]
        )

        rerank_latency = safe_float(
            row["rerank_latency_ms"]
        )

        total_latency = (
            detection_latency
            + retrieval_latency
            + plugin_latency
            + rerank_latency
        )

        timing_text = f"""
PERFORMANCE

Detection
    {detection_latency:.2f} ms

Retrieval
    {retrieval_latency:.2f} ms

Plugins
    {plugin_latency:.2f} ms

Reranker
    {rerank_latency:.2f} ms

────────────────

TOTAL
    {total_latency:.2f} ms
"""

        ax_timing.text(
            0.08,
            TEXT_Y,
            timing_text,
            va="top",
            fontsize=FONT_INFO,
            family="monospace",
            transform=ax_timing.transAxes
        )

        ax_timing.set_title(
            "5. PERFORMANCE",
            fontsize=FONT_SUBTITLE,
            pad=TITLE_PAD
        )

        ax_timing.axis("off")

        # ====================================================
        # 6. FINAL DECISION
        # ====================================================

        final_product = safe_value(
            row["final_product_id"],
            "N/A"
        )

        final_status = safe_value(
            row["final_status"],
            "N/A"
        )

        final_confidence = format_number(
            row["final_confidence"],
            4
        )

        correct = parse_bool(
            row["correct"]
        )

        final_symbol = (
            "✓ CORRECT"
            if correct
            else "✗ WRONG"
        )

        changed_by_plugin = safe_value(
            row["changed_by_plugin"],
            "False"
        )

        final_text = f"""
FINAL RESULT

GT PRODUCT
    {gt_product}

FINAL PRODUCT
    {final_product}

STATUS
    {final_status}

CONFIDENCE
    {final_confidence}

────────────────

RESULT
    {final_symbol}

Changed by Plugin
    {changed_by_plugin}
"""

        ax_final.text(
            TEXT_X,
            TEXT_Y,
            final_text,
            va="top",
            fontsize=FONT_INFO,
            family="monospace",
            transform=ax_final.transAxes
        )

        ax_final.set_title(
            "6. FINAL DECISION",
            fontsize=FONT_SUBTITLE,
            pad=TITLE_PAD
        )

        ax_final.axis("off")

        # ====================================================
        # GLOBAL TITLE
        # ====================================================

        category_id = safe_int(
            row["gt_product_id"]
        )

        if category_id is not None:

            category_name = (
                sample["categories"]
                .get(
                    category_id,
                    {}
                )
                .get(
                    "name",
                    ""
                )
            )

        else:

            category_name = ""

        image_name = Path(
            str(row["source_path"])
        ).name

        self.fig.suptitle(
            (
                f"VAL PRODUCT VIEWER   "
                f"[{self.index + 1}/{len(self.samples)}]\n"
                f"Label: {gt_product} "
                f"{category_name}   |   "
                f"Image: {image_name}   |   "
                f"Crop ID: {row['crop_id']}"
            ),
            fontsize=FONT_TITLE,
            y=0.97
        )

        # ====================================================
        # FOOTER
        # ====================================================

        self.fig.text(
            0.5,
            0.015,
            "← / → : Previous / Next   |   Home/End : First/Last   |   S : Save   |   ESC : Exit",
            ha="center",
            fontsize=FONT_FOOTER
        )

        # ====================================================
        # MANUAL FIGURE SPACING
        # ====================================================

        # Không dùng tight_layout().
        #
        # Dùng subplots_adjust để tránh title/text
        # bị chồng lên nhau.

        self.fig.subplots_adjust(
            top=0.88,
            bottom=0.065,
            left=0.035,
            right=0.965,
            wspace=PANEL_WSPACE,
            hspace=PANEL_HSPACE
        )

        self.fig.canvas.draw_idle()

    # ========================================================
    # KEYBOARD
    # ========================================================

    def on_key(
        self,
        event
    ):

        # ----------------------------------------------------
        # NEXT
        # ----------------------------------------------------

        if event.key == "right":

            self.index += 1

            if self.index >= len(
                self.samples
            ):

                self.index = 0

            self.update()

        # ----------------------------------------------------
        # PREVIOUS
        # ----------------------------------------------------

        elif event.key == "left":

            self.index -= 1

            if self.index < 0:

                self.index = (
                    len(self.samples) - 1
                )

            self.update()

        # ----------------------------------------------------
        # EXIT
        # ----------------------------------------------------

        elif event.key == "escape":

            plt.close(
                self.fig
            )

        # ----------------------------------------------------
        # HOME / END (thêm mới, đồng nhất với các viewer khác)
        # ----------------------------------------------------

        elif event.key == "home":

            self.index = 0
            self.update()

        elif event.key == "end":

            self.index = len(self.samples) - 1
            self.update()

        # ----------------------------------------------------
        # SAVE (thêm mới, đồng nhất với các viewer khác)
        # ----------------------------------------------------

        elif event.key == "s":

            out_dir = ROOT_DIR / "data" / "outputs" / "debug"
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / f"val_dashboard_frame_{self.index:04d}.png"
            self.fig.savefig(out_path, dpi=150, bbox_inches="tight")
            print(f"[SAVED] {out_path}")


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 70)
    print("VAL PRODUCT VIEWER")
    print("=" * 70)

    print(
        "ROOT:",
        ROOT_DIR
    )

    print(
        "COCO:",
        COCO_FILE
    )

    print(
        "CSV:",
        VAL_CSV
    )

    print(
        "IMAGES:",
        IMAGES_DIR
    )

    print(
        "TARGET LABELS:",
        TARGET_LABELS
    )
    
    print(
        "ONLY WRONG:",
        ONLY_WRONG
    )

    print(
        "FIGSIZE:",
        FIGSIZE
    )

    print(
        "DISPLAY ORIGINAL:",
        DISPLAY_ORIGINAL_MAX_WIDTH,
        "x",
        DISPLAY_ORIGINAL_MAX_HEIGHT
    )

    print(
        "DISPLAY CROP:",
        DISPLAY_CROP_MAX_WIDTH,
        "x",
        DISPLAY_CROP_MAX_HEIGHT
    )

    print("=" * 70)

    # ========================================================
    # VALIDATE PATHS
    # ========================================================

    if not COCO_FILE.exists():

        raise FileNotFoundError(
            f"Không tìm thấy COCO:\n{COCO_FILE}"
        )

    if not VAL_CSV.exists():

        raise FileNotFoundError(
            f"Không tìm thấy VAL CSV:\n{VAL_CSV}"
        )

    if not IMAGES_DIR.exists():

        raise FileNotFoundError(
            f"Không tìm thấy images:\n{IMAGES_DIR}"
        )

    # ========================================================
    # BUILD SAMPLES
    # ========================================================

    samples = build_samples()

    print(
        f"\nFound {len(samples)} samples."
    )

    if not samples:

        print(
            "\nKhông có sample nào "
            "thuộc TARGET_LABELS."
        )

        print(
            "TARGET_LABELS =",
            TARGET_LABELS
        )

        return

    # ========================================================
    # STATISTICS
    # ========================================================

    print("\nSamples by label:")

    df = pd.DataFrame([
        s["row"]
        for s in samples
    ])

    counts = (
        df["gt_product_id"]
        .value_counts()
        .sort_index()
    )

    for label, count in counts.items():

        print(
            f"  Label {label}: {count}"
        )

    print("=" * 70)

    # ========================================================
    # START VIEWER
    # ========================================================

    viewer = VALViewer(
        samples
    )

    viewer.show()


# ============================================================
# ENTRY
# ============================================================

if __name__ == "__main__":
    main()