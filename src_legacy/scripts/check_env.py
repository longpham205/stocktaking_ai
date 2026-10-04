"""Kiểm tra môi trường trước khi chạy hoặc demo web POS (chỉ xác minh, KHÔNG cài đặt, KHÔNG build).

    python scripts/check_env.py --config configs/config.demo.yaml --data-dir data_demo [--port 8000]

In bảng OK / CẢNH BÁO / LỖI kèm gợi ý cách sửa. Mã thoát: 0 nếu không có LỖI (cảnh báo vẫn cho chạy), 1 nếu có LỖI.
`launch.bat` / `launch.sh` gọi script này trước khi khởi động server.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import socket
import sqlite3
import sys
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
OK, WARN, FAIL = "OK", "CẢNH BÁO", "LỖI"
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def _load_db_snapshot():
    spec = importlib.util.spec_from_file_location("db_snapshot", Path(__file__).with_name("db_snapshot.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _under(root: Path, p: str | Path) -> Path:
    p = Path(p)
    return p if p.is_absolute() else root / p


def _find_product_ids(node, out: set[str]) -> None:
    """Gom mọi giá trị khoá 'product_id' trong cấu trúc JSON bất kỳ (chịu được thay đổi định dạng metadata)."""
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "product_id" and v is not None:
                out.add(str(v))
            else:
                _find_product_ids(v, out)
    elif isinstance(node, list):
        for v in node:
            _find_product_ids(v, out)


def run_checks(
    config_path: Path,
    data_dir: Path,
    port: int = 8000,
    root: Path = ROOT,
    has_module: Callable[[str], bool] | None = None,
    port_busy: Callable[[int], bool] | None = None,
    fake: bool = False,
) -> list[tuple[str, str, str]]:
    """Trả danh sách (mức, tên kiểm tra, chi tiết/gợi ý). `has_module`/`port_busy` cho phép test thay thế.

    `fake=True` (web chạy với --fake, không nạp pipeline): bỏ qua gallery/index/weights và các gói AI nặng."""
    import yaml  # PyYAML nằm trong requirements.txt; thiếu thì lỗi rõ ràng ở bước kiểm tra gói bên dưới

    has_module = has_module or (lambda n: importlib.util.find_spec(n) is not None)
    results: list[tuple[str, str, str]] = []
    add = lambda lvl, name, detail="": results.append((lvl, name, detail))  # noqa: E731

    # ---- Python và venv
    v = sys.version_info
    add(OK if v >= (3, 10) else FAIL, "Phiên bản Python", f"{v.major}.{v.minor}.{v.micro}" + ("" if v >= (3, 10) else " — cần >= 3.10"))
    in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
    add(OK if in_venv else WARN, "Môi trường ảo (venv)", sys.prefix if in_venv else "đang chạy ngoài venv — hãy kích hoạt venv của dự án để tránh lệch thư viện")

    # ---- Config
    cfg_path = _under(root, config_path)
    if not cfg_path.is_file():
        add(FAIL, "File config", f"không thấy {cfg_path}")
        return results
    try:
        cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        add(FAIL, "File config", f"YAML không hợp lệ: {exc}")
        return results
    add(OK, "File config", str(cfg_path))

    det_backend = str(cfg.get("detection", {}).get("backend", ""))
    ret_backend = str(cfg.get("retrieval", {}).get("backend", ""))
    ref_cfg = cfg.get("refinement", {})
    ref_backend = str(ref_cfg.get("backend", "none")) if ref_cfg.get("enabled", True) else "none"
    ocr_on = bool(cfg.get("plugins", {}).get("ocr", {}).get("enabled", False))
    bar_on = bool(cfg.get("plugins", {}).get("barcode", {}).get("enabled", False))
    mock = lambda b: b.startswith("mock") or b == "none"  # noqa: E731
    add(OK, "Backend nhận diện", f"detector={det_backend or '?'} · retrieval={ret_backend or '?'} · refinement={ref_backend}"
        + ("  (CHẾ ĐỘ MOCK — kết quả chỉ để thử, không phải model thật)" if mock(det_backend) or mock(ret_backend) else ""))

    # ---- Thư viện
    need = {"cv2": "opencv-python-headless", "numpy": "numpy", "yaml": "PyYAML"}
    if not fake:
        need["faiss"] = "faiss-cpu"
    if fake:
        pass
    elif not mock(det_backend):
        need.update({"torch": "torch", "rfdetr": "rfdetr"})
    if not fake and not mock(ret_backend):
        need.update({"torch": "torch", "transformers": "transformers"})
    if not fake and ref_backend == "sam2":
        need.update({"torch": "torch", "sam2": "sam2"})
    if ocr_on and not fake:
        need["easyocr"] = "easyocr"
    missing = {m: pkg for m, pkg in need.items() if not has_module(m)}
    add(FAIL if missing else OK, "Thư viện Python",
        ("thiếu: " + ", ".join(f"{m} (pip install {p})" for m, p in missing.items())) if missing else f"đủ {len(need)} gói cần thiết")
    if bar_on and not fake and not has_module("pyzbar"):
        add(WARN, "Thư viện pyzbar", "plugin barcode đang bật nhưng thiếu pyzbar — bước đọc mã vạch sẽ không hoạt động (pip install pyzbar; Linux cần thêm gói hệ thống libzbar0)")

    # ---- Catalog, gallery, index, weights
    paths = cfg.get("paths", {})
    # Catalog (Phase 1B): web + pipeline đọc bảng catalog trong DB (catalog.source = sqlite). Bắt buộc kể cả --fake.
    ccfg = cfg.get("catalog", {})
    catalog_ids: set[str] = set()
    cat_db: Path | None = None
    if ccfg.get("source") != "sqlite" or not ccfg.get("db_path"):
        add(FAIL, "Catalog sản phẩm", "config cần catalog.source: sqlite và catalog.db_path (web đọc catalog từ DB)")
    else:
        cat_db = _under(root, ccfg["db_path"])
        try:
            con = sqlite3.connect(f"file:{cat_db.as_posix()}?mode=ro", uri=True)
            try:
                catalog_ids = {str(r[0]) for r in con.execute("SELECT product_id FROM product WHERE is_active = 1")}
            finally:
                con.close()
            add(OK if catalog_ids else FAIL, "Catalog sản phẩm",
                f"{len(catalog_ids)} SKU ({cat_db.name})" if catalog_ids else f"{cat_db} chưa có SKU — chạy python -m src.catalog.migrate ...")
        except sqlite3.Error as exc:
            add(FAIL, "Catalog sản phẩm", f"không đọc được bảng product trong {cat_db}: {exc} — chạy python -m src.catalog.migrate ...")

    gallery_dir = _under(root, paths.get("gallery_dir", "data/gallery"))
    folders = [d for d in gallery_dir.iterdir() if d.is_dir()] if gallery_dir.is_dir() else []
    n_imgs = sum(1 for d in folders for f in d.iterdir() if f.suffix.lower() in IMAGE_EXT)
    if fake:
        add(OK, "Pipeline AI", "chế độ --fake: bỏ qua gallery, index FAISS và weights")
    elif not folders or not n_imgs:
        add(FAIL, "Gallery ảnh sản phẩm", f"không có ảnh trong {gallery_dir}")
    else:
        add(OK, "Gallery ảnh sản phẩm", f"{len(folders)} thư mục, {n_imgs} ảnh")

    rcfg = cfg.get("retrieval", {})
    if fake:
        rcfg = {}
    index_file = _under(root, rcfg.get("gallery_index_path", "data/cache/gallery_index.faiss"))
    meta_file = _under(root, rcfg.get("gallery_metadata_path", "data/cache/gallery_metadata.json"))
    if fake:
        pass
    elif index_file.is_file() and meta_file.is_file():
        add(OK, "Index FAISS của gallery", f"{index_file.stat().st_size / 1024:.0f} KB")
        try:
            in_index: set[str] = set()
            _find_product_ids(json.loads(meta_file.read_text(encoding="utf-8")), in_index)
            ghost = sorted(in_index - catalog_ids, key=lambda x: (len(x), x)) if catalog_ids and in_index else []
            if ghost:
                add(WARN, "Index ↔ catalog", f"index chứa SKU không có trong catalog: {', '.join(ghost[:10])} — nên build lại index")
        except (ValueError, OSError):
            add(WARN, "Metadata index", f"không đọc được {meta_file.name}")
    elif rcfg.get("build_gallery_index", False):
        add(WARN, "Index FAISS của gallery", "chưa có — sẽ được build ở lần chạy `run.py` đầu tiên (có thể mất thời gian)")
    else:
        add(FAIL, "Index FAISS của gallery", f"không thấy {index_file.name}/{meta_file.name} — chạy `python run.py --mode validate --config ...` để build")

    if fake:
        pass
    if not fake and det_backend == "rf_detr":
        w = _under(root, cfg.get("detection", {}).get("rf_detr", {}).get("weights_path", ""))
        add(OK if w.is_file() else FAIL, "Weights detector (RF-DETR)", str(w) if w.is_file() else f"không thấy {w}")
    if not fake and ref_backend == "sam2":
        w = _under(root, ref_cfg.get("sam2", {}).get("checkpoint_path", ""))
        add(OK if w.is_file() else FAIL, "Checkpoint SAM2", str(w) if w.is_file() else f"không thấy {w}")
    if not fake and ret_backend == "siglip2":
        w = _under(root, cfg.get("retrieval", {}).get("siglip2", {}).get("weights_path", ""))
        add(OK if w.exists() else WARN, "Weights embedding (SigLIP2)", str(w) if w.exists() else f"không thấy {w} — sẽ cố tải từ internet lần đầu")

    # ---- Web: thư mục dữ liệu, .env, cổng, DB
    base = _under(root, data_dir)
    try:
        base.mkdir(parents=True, exist_ok=True)
        probe = base / ".write_test"
        probe.write_text("x", encoding="utf-8")
        probe.unlink()
        add(OK, "Thư mục dữ liệu web", f"{base} ghi được")
    except OSError as exc:
        add(FAIL, "Thư mục dữ liệu web", f"không ghi được {base}: {exc}")
    env_file = root / ".env"
    add(OK if env_file.is_file() else WARN, "File .env (bí mật)",
        "có" if env_file.is_file() else "chưa có — sẽ tự sinh mật khẩu staff/admin ngẫu nhiên lần chạy đầu và in ra MỘT lần")
    busy = port_busy or _load_db_snapshot().server_running
    add(FAIL if busy(port) else OK, f"Cổng {port}", "đang bị tiến trình khác chiếm — tắt tiến trình đó hoặc dùng --port khác" if busy(port) else "trống")

    db_file = base / "db" / "app.db"
    if db_file.is_file():
        try:
            conn = sqlite3.connect(f"file:{db_file}?mode=ro", uri=True, timeout=5)
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            # DB có thể chỉ mới có bảng catalog (migrate xong, web chưa chạy lần nào): bảng web sẽ được tạo lần chạy đầu.
            web_ready = bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='users'").fetchone())
            users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] if web_ready else 0
            priced = {r[0] for r in conn.execute("SELECT product_id FROM product_prices")} if web_ready else set()
            conn.close()
            detail = (f"{db_file.name}: {users} tài khoản" if web_ready else f"{db_file.name}: chưa có bảng web — sẽ tạo ở lần chạy đầu")
            add(OK if integrity == "ok" else FAIL, "Database web", detail if integrity == "ok" else f"không toàn vẹn: {integrity}")
            if catalog_ids:
                no_price = len(catalog_ids - priced)
                add(OK if not no_price else WARN, "Giá sản phẩm",
                    "mọi SKU đã có giá" if not no_price else f"{no_price}/{len(catalog_ids)} SKU chưa có giá — thu ngân sẽ phải nhập giá tay (Admin → Sản phẩm → lọc 'Thiếu giá')")
                bc = sqlite3.connect(f"file:{cat_db.as_posix()}?mode=ro", uri=True)
                try:
                    with_bc = {str(r[0]) for r in bc.execute("SELECT product_id FROM product WHERE barcode IS NOT NULL AND barcode <> ''")}
                finally:
                    bc.close()
                add(OK, "Barcode", f"{len(with_bc & catalog_ids)}/{len(catalog_ids)} SKU có barcode (chỉ SKU có barcode mới quét được)")
        except sqlite3.Error as exc:
            add(FAIL, "Database web", f"không mở được {db_file}: {exc}")
    else:
        add(OK, "Database web", "chưa có — sẽ được tạo ở lần chạy đầu (giá seed nạp từ data/seed/product_prices.json nếu có)")
    return results


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Kiểm tra môi trường trước khi chạy/demo web POS")
    ap.add_argument("--config", default="configs/config.yaml")
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--fake", action="store_true", help="web chạy với bộ nhận diện giả: bỏ qua kiểm tra pipeline AI")
    # launch.bat/launch.sh chuyển nguyên các tham số của backend (--host, -v, ...): tham số lạ được bỏ qua
    args, _unknown = ap.parse_known_args(argv)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    results = run_checks(Path(args.config), Path(args.data_dir), args.port, fake=args.fake)
    width = max(len(n) for _, n, _ in results)
    mark = {OK: "[ OK ]", WARN: "[WARN]", FAIL: "[FAIL]"}
    for lvl, name, detail in results:
        print(f"{mark[lvl]} {name:<{width}}  {detail}")
    fails = sum(1 for lvl, *_ in results if lvl == FAIL)
    warns = sum(1 for lvl, *_ in results if lvl == WARN)
    print(f"\n{'KHÔNG SẴN SÀNG' if fails else 'SẴN SÀNG'}: {fails} lỗi, {warns} cảnh báo")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
