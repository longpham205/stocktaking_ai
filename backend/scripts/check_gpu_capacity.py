
"""
Stress test VRAM cho Stocktaking AI.

Chạy nhiều ảnh, mỗi ảnh lặp lại nhiều lần để kiểm tra:
1. Ảnh nhiều sản phẩm có gây CUDA OOM không.
2. VRAM có tăng dần qua các lần gọi hay không.
3. Pipeline có ổn định khi chạy liên tiếp hay không.

Cách dùng:

    python scripts/check_gpu_capacity.py

    python scripts/check_gpu_capacity.py --repeats 5

    python scripts/check_gpu_capacity.py --clear-cache

    python scripts/check_gpu_capacity.py --summary-only

    python scripts/check_gpu_capacity.py --image-dir "D:\\test_images"

Đường dẫn mặc định được cấu hình tại:
    DEFAULT_IMAGE_DIR
"""

import argparse
import glob
import os
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
for _stream in (sys.stdout, sys.stderr):  # Windows cp1252 khi pipe không in được tiếng Việt
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from engine.models.models import ImageData  # noqa: E402


# ============================================================
# CẤU HÌNH
# ============================================================

# >>> SỬA ĐƯỜNG DẪN TEST TẠI ĐÂY <<<
DEFAULT_IMAGE_DIR = str(ROOT / "data" / "benchmark" / "test")

# Số lần chạy mặc định cho mỗi ảnh
DEFAULT_REPEATS = 3

# Clear CUDA cache sau mỗi lần chạy
DEFAULT_CLEAR_CACHE = False

# Chỉ in kết quả tổng hợp, không in log từng lần chạy
DEFAULT_SUMMARY_ONLY = False

IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png")


# ============================================================
# HELPER
# ============================================================

def to_mb(num_bytes: int) -> float:
    return num_bytes / (1024 ** 2)


def print_header(title: str) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


# ============================================================
# CUDA
# ============================================================

def check_torch_cuda():
    if not summary_only:
        print_header("1. Kiểm tra Torch & CUDA")

    try:
        import torch
    except ImportError:
        print(
            "Không import được torch. "
            "Hãy activate đúng venv của project."
        )
        sys.exit(1)

    cuda_ok = torch.cuda.is_available()

    if not summary_only:
        print(f"Torch version: {torch.__version__}")
        print(f"CUDA available: {cuda_ok}")

    if not cuda_ok:
        if not summary_only:
            print(
                "Không thấy GPU. Pipeline sẽ chạy trên CPU."
            )
        return None

    props = torch.cuda.get_device_properties(0)
    total_vram = to_mb(props.total_memory)

    if not summary_only:
        print(f"GPU: {props.name}")
        print(
            f"Tổng VRAM: {total_vram:.0f} MB "
            f"(~{total_vram / 1024:.1f} GB)"
        )

    return torch


# ============================================================
# LOAD PIPELINE
# ============================================================

def load_pipeline():
    if not summary_only:
        print_header(
            "2. Load config & khởi tạo pipeline"
        )

    try:
        from engine.core.config import load_config
        from engine.pipeline.pipeline import InventoryPipeline
    except ImportError:
        print(
            "Không import được engine.core.config / "
            "engine.pipeline.pipeline."
        )
        print(
            "Hãy chạy script từ thư mục gốc project."
        )
        sys.exit(1)

    config = load_config()
    pipeline = InventoryPipeline(config)

    if not summary_only:
        print("Pipeline đã load xong.")

    return pipeline


# ============================================================
# TÌM ẢNH
# ============================================================

def find_images(image_dir: str) -> list[str]:

    image_dir = os.path.abspath(image_dir)

    if not os.path.isdir(image_dir):
        print(f"\nKhông tồn tại thư mục: {image_dir}")
        return []

    paths = []

    for path in sorted(glob.glob(os.path.join(image_dir, "*"))):
        if (
            os.path.isfile(path)
            and path.lower().endswith(IMAGE_EXTENSIONS)
        ):
            paths.append(path)

    return paths


# ============================================================
# LOAD IMAGE
# ============================================================

def load_image_data(
    image_path: str,
) -> "ImageData | None":

    import cv2

    image = cv2.imread(image_path)

    if image is None:
        return None

    height, width = image.shape[:2]

    return ImageData(
        image_id=os.path.splitext(
            os.path.basename(image_path)
        )[0],
        source_path=os.path.abspath(image_path),
        image_array=image,
        width=width,
        height=height,
    )


# ============================================================
# CHẠY PIPELINE 1 LẦN
# ============================================================

def run_one(
    pipeline,
    torch_mod,
    image_data: ImageData,
    clear_cache: bool,
) -> dict:

    if torch_mod is not None:
        torch_mod.cuda.reset_peak_memory_stats()

    try:
        result = pipeline.run(image_data)

    except Exception as exc:

        is_oom = (
            "out of memory" in str(exc).lower()
            or "cuda error" in str(exc).lower()
        )

        return {
            "success": False,
            "oom": is_oom,
            "error": f"{type(exc).__name__}: {exc}",
            "item_count": 0,
            "allocated_mb": 0.0,
            "reserved_mb": 0.0,
        }

    stats = {
        "success": True,
        "oom": False,
        "error": None,
        "item_count": len(result.items),
        "allocated_mb": 0.0,
        "reserved_mb": 0.0,
    }

    if torch_mod is not None:

        stats["allocated_mb"] = to_mb(
            torch_mod.cuda.max_memory_allocated()
        )

        stats["reserved_mb"] = to_mb(
            torch_mod.cuda.max_memory_reserved()
        )

        if clear_cache:
            torch_mod.cuda.empty_cache()

    return stats


# ============================================================
# STRESS TEST 1 ẢNH
# ============================================================

def stress_test_image(
    pipeline,
    torch_mod,
    image_path: str,
    repeats: int,
    clear_cache: bool,
) -> dict:

    image_name = os.path.basename(image_path)

    if not summary_only:
        print_header(f"Ảnh: {image_name}")

    image_data = load_image_data(image_path)

    if image_data is None:

        if not summary_only:
            print("Không đọc được ảnh — bỏ qua.")

        return {
            "image": image_name,
            "runs": 0,
            "success": 0,
            "failed": 1,
            "oom": 0,
            "item_count": None,
            "first_allocated": None,
            "last_allocated": None,
            "first_reserved": None,
            "last_reserved": None,
            "max_allocated": None,
            "max_reserved": None,
        }

    runs = []

    for i in range(1, repeats + 1):

        stats = run_one(
            pipeline,
            torch_mod,
            image_data,
            clear_cache,
        )

        runs.append(stats)

        if not summary_only:

            if stats["success"]:

                if torch_mod is not None:
                    print(
                        f"  Lần {i}/{repeats}: "
                        f"{stats['item_count']} sản phẩm | "
                        f"allocated="
                        f"{stats['allocated_mb']:.0f}MB | "
                        f"reserved="
                        f"{stats['reserved_mb']:.0f}MB"
                    )
                else:
                    print(
                        f"  Lần {i}/{repeats}: "
                        f"{stats['item_count']} sản phẩm"
                    )

            else:

                if stats["oom"]:
                    print(
                        f"  Lần {i}/{repeats}: "
                        f"CUDA OUT OF MEMORY"
                    )
                else:
                    print(
                        f"  Lần {i}/{repeats}: "
                        f"LỖI {stats['error']}"
                    )

    # ========================================================
    # TỔNG HỢP ẢNH
    # ========================================================

    successful = [
        x for x in runs
        if x["success"]
    ]

    failed = [
        x for x in runs
        if not x["success"]
    ]

    oom_count = sum(
        1 for x in runs
        if x["oom"]
    )

    if successful:

        allocated_values = [
            x["allocated_mb"]
            for x in successful
        ]

        reserved_values = [
            x["reserved_mb"]
            for x in successful
        ]

        first_allocated = allocated_values[0]
        last_allocated = allocated_values[-1]

        first_reserved = reserved_values[0]
        last_reserved = reserved_values[-1]

        max_allocated = max(allocated_values)
        max_reserved = max(reserved_values)

        item_counts = [
            x["item_count"]
            for x in successful
        ]

        item_count = item_counts[0]

    else:

        first_allocated = None
        last_allocated = None
        first_reserved = None
        last_reserved = None
        max_allocated = None
        max_reserved = None
        item_count = None

    result = {
        "image": image_name,
        "runs": len(runs),
        "success": len(successful),
        "failed": len(failed),
        "oom": oom_count,
        "item_count": item_count,
        "first_allocated": first_allocated,
        "last_allocated": last_allocated,
        "first_reserved": first_reserved,
        "last_reserved": last_reserved,
        "max_allocated": max_allocated,
        "max_reserved": max_reserved,
    }

    return result


# ============================================================
# IN RA TÓM TẮT
# ============================================================

def print_summary(
    results: list[dict],
    image_dir: str,
    repeats: int,
    torch_mod,
    clear_cache: bool,
) -> None:

    print_header("TÓM TẮT KẾT QUẢ STRESS TEST")

    total_images = len(results)

    total_runs = sum(
        r["runs"]
        for r in results
    )

    total_success = sum(
        r["success"]
        for r in results
    )

    total_failed = sum(
        r["failed"]
        for r in results
    )

    total_oom = sum(
        r["oom"]
        for r in results
    )

    print(f"Thư mục test       : {image_dir}")
    print(f"Số ảnh             : {total_images}")
    print(f"Số lần lặp / ảnh   : {repeats}")
    print(f"Tổng lượt chạy     : {total_runs}")
    print(f"Thành công         : {total_success}")
    print(f"Thất bại           : {total_failed}")
    print(f"CUDA OOM           : {total_oom}")
    print(
        f"Clear CUDA cache   : "
        f"{'Có' if clear_cache else 'Không'}"
    )

    # --------------------------------------------------------
    # BẢNG TỪNG ẢNH
    # --------------------------------------------------------

    print("\n" + "-" * 78)

    print(
        f"{'Ảnh':<28}"
        f"{'SP':>6}"
        f"{'OK':>5}"
        f"{'OOM':>6}"
        f"{'Alloc':>10}"
        f"{'Reserved':>11}"
        f"{'ΔReserved':>12}"
    )

    print("-" * 78)

    for r in results:

        image_name = r["image"]

        if len(image_name) > 27:
            image_name = image_name[:24] + "..."

        item_count = (
            str(r["item_count"])
            if r["item_count"] is not None
            else "-"
        )

        if r["first_allocated"] is not None:

            allocated_text = (
                f"{r['first_allocated']:.0f}"
                f"->{r['last_allocated']:.0f}"
            )

            reserved_text = (
                f"{r['first_reserved']:.0f}"
                f"->{r['last_reserved']:.0f}"
            )

            delta_reserved = (
                r["last_reserved"]
                - r["first_reserved"]
            )

            delta_text = f"{delta_reserved:+.0f} MB"

        else:

            allocated_text = "-"
            reserved_text = "-"
            delta_text = "-"

        print(
            f"{image_name:<28}"
            f"{item_count:>6}"
            f"{r['success']:>5}"
            f"{r['oom']:>6}"
            f"{allocated_text:>10}"
            f"{reserved_text:>11}"
            f"{delta_text:>12}"
        )

    print("-" * 78)

    # --------------------------------------------------------
    # PHÂN TÍCH VRAM
    # --------------------------------------------------------

    valid_results = [
        r for r in results
        if r["first_reserved"] is not None
    ]

    if not valid_results:

        print("\nKhông có đủ dữ liệu VRAM để đánh giá.")

    else:

        first_reserved_values = [
            r["first_reserved"]
            for r in valid_results
        ]

        last_reserved_values = [
            r["last_reserved"]
            for r in valid_results
        ]

        max_reserved_values = [
            r["max_reserved"]
            for r in valid_results
        ]

        global_first_reserved = min(
            first_reserved_values
        )

        global_last_reserved = max(
            last_reserved_values
        )

        global_max_reserved = max(
            max_reserved_values
        )

        print(
            f"\nReserved VRAM lớn nhất : "
            f"{global_max_reserved:.0f} MB"
        )

        print(
            f"Reserved đầu/cuối     : "
            f"{global_first_reserved:.0f} -> "
            f"{global_last_reserved:.0f} MB"
        )

    # --------------------------------------------------------
    # KẾT LUẬN
    # --------------------------------------------------------

    print("\n" + "-" * 78)
    print("KẾT LUẬN")

    if total_oom > 0:

        print(
            "  [CẢNH BÁO] Phát hiện CUDA OUT OF MEMORY."
        )

        print(
            "  Pipeline chưa ổn định với workload hiện tại."
        )

    elif total_failed > 0:

        print(
            "  [CẢNH BÁO] Có lượt chạy thất bại "
            "nhưng không phải CUDA OOM."
        )

        print(
            "  Cần kiểm tra error log tương ứng."
        )

    else:

        leak_detected = False

        for r in valid_results:

            if r["first_reserved"] <= 0:
                continue

            growth_pct = (
                (
                    r["last_reserved"]
                    - r["first_reserved"]
                )
                / r["first_reserved"]
                * 100
            )

            if growth_pct > 5:
                leak_detected = True
                break

        if leak_detected:

            print(
                "  [CẢNH BÁO] Reserved VRAM tăng "
                "đáng kể qua các lần chạy."
            )

            print(
                "  Nên kiểm tra thêm với --clear-cache."
            )

        else:

            print(
                "  [ỔN] Không phát hiện CUDA OOM."
            )

            print(
                "  [ỔN] Không phát hiện VRAM tăng "
                "đáng kể qua các lần chạy."
            )

            print(
                "  Pipeline hoạt động ổn định "
                "trong bài stress test."
            )

    print("-" * 78)

    # --------------------------------------------------------
    # GỢI Ý
    # --------------------------------------------------------

    if not clear_cache and torch_mod is not None:

        print(
            "\nGợi ý: chạy thêm:"
        )

        print(
            "  python scripts/check_gpu_capacity.py --clear-cache"
        )

        print(
            "để so sánh mức Reserved VRAM "
            "sau khi giải phóng CUDA cache."
        )


# ============================================================
# MAIN
# ============================================================

def main():

    global summary_only

    parser = argparse.ArgumentParser(
        description=(
            "Stress test VRAM cho pipeline "
            "Stocktaking AI."
        )
    )

    parser.add_argument(
        "--image-dir",
        type=str,
        default=None,
        help=(
            "Thư mục chứa ảnh test. "
            "Nếu không truyền sẽ dùng "
            "DEFAULT_IMAGE_DIR."
        ),
    )

    parser.add_argument(
        "--repeats",
        type=int,
        default=DEFAULT_REPEATS,
        help=(
            f"Số lần lặp / ảnh. "
            f"Mặc định: {DEFAULT_REPEATS}."
        ),
    )

    parser.add_argument(
        "--clear-cache",
        action="store_true",
        default=DEFAULT_CLEAR_CACHE,
        help="Clear CUDA cache sau mỗi lần chạy.",
    )

    parser.add_argument(
        "--summary-only",
        action="store_true",
        default=DEFAULT_SUMMARY_ONLY,
        help=(
            "Chỉ in phần tóm tắt cuối, "
            "ẩn log từng lần chạy."
        ),
    )

    args = parser.parse_args()

    summary_only = args.summary_only

    # ========================================================
    # XÁC ĐỊNH ĐƯỜNG DẪN
    # ========================================================

    if args.image_dir is not None:
        image_dir = args.image_dir
    else:
        image_dir = DEFAULT_IMAGE_DIR

    image_dir = os.path.abspath(image_dir)

    if not summary_only:

        print_header("CẤU HÌNH STRESS TEST")

        print(f"Thư mục ảnh:")
        print(f"  {image_dir}")

        print(f"Số lần lặp / ảnh:")
        print(f"  {args.repeats}")

        print(
            f"Clear CUDA cache: "
            f"{args.clear_cache}"
        )

    # ========================================================
    # KIỂM TRA THAM SỐ
    # ========================================================

    if args.repeats < 1:

        print(
            "Lỗi: --repeats phải >= 1."
        )

        sys.exit(1)

    # ========================================================
    # CUDA
    # ========================================================

    torch_mod = check_torch_cuda()

    # ========================================================
    # LOAD PIPELINE
    # ========================================================

    pipeline = load_pipeline()

    # ========================================================
    # TÌM ẢNH
    # ========================================================

    image_paths = find_images(image_dir)

    if not image_paths:

        print(
            f"\nKhông tìm thấy ảnh "
            f"(.jpg/.jpeg/.png) trong:"
        )

        print(f"  {image_dir}")

        print(
            "\nKiểm tra lại DEFAULT_IMAGE_DIR "
            "hoặc dùng --image-dir."
        )

        return

    if not summary_only:

        print(
            f"\nTìm thấy {len(image_paths)} ảnh."
        )

    # ========================================================
    # CHẠY TEST
    # ========================================================

    results = []

    for image_path in image_paths:

        result = stress_test_image(
            pipeline=pipeline,
            torch_mod=torch_mod,
            image_path=image_path,
            repeats=args.repeats,
            clear_cache=args.clear_cache,
        )

        results.append(result)

    # ========================================================
    # TÓM TẮT CUỐI
    # ========================================================

    print_summary(
        results=results,
        image_dir=image_dir,
        repeats=args.repeats,
        torch_mod=torch_mod,
        clear_cache=args.clear_cache,
    )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    summary_only = False

    main()

