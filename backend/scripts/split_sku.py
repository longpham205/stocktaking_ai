"""Tách một SKU đang gộp nhầm hai sản phẩm: chuyển một phần ảnh gallery sang một SKU mới.

Chạy từ backend/ (tắt web nhận diện thật trước: nó đang giữ catalog và index cũ):

    # mở cửa sổ, bấm chọn những ảnh thuộc sản phẩm cần TÁCH RA
    python scripts/split_sku.py --product 36

    # hoặc không mở cửa sổ: nêu tên file
    python scripts/split_sku.py --product 36 --move 0007.jpg 0008.jpg --name "Sữa rửa mặt B"

Việc script làm:
    - cấp mã mới (``next_product_id``), tạo thư mục ``data/gallery/<mã 4 chữ số>/`` và chuyển ảnh đã chọn sang;
    - ghi SKU mới + sửa số ảnh của SKU cũ trong catalog của pipeline (SQLite) VÀ catalog của web
      (Postgres, lấy theo ``.env`` như API); hai nơi phải cấp cùng một mã, lệch thì dừng trước khi đổi gì.
      ``--no-web-db`` bỏ qua catalog của web (máy chỉ chạy pipeline).

Việc script KHÔNG làm (in ra ở cuối): lập lại index + chữ ký màu; đặt giá / bằng chứng cho SKU mới;
sửa các nhãn, dòng đơn hàng cũ đang mang mã SKU cũ.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def gallery_images(folder: Path) -> list[str]:
    return sorted(p.name for p in folder.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES)


def check_selection(files: list[str], chosen: list[str]) -> list[str]:
    """Danh sách ảnh sẽ chuyển (không trùng, theo thứ tự tên); lỗi nếu không tách được."""
    moved = sorted(set(chosen))
    unknown = [name for name in moved if name not in files]
    if unknown:
        raise ValueError(f"Không có trong thư mục gallery: {', '.join(unknown)}")
    if not moved:
        raise ValueError("Chưa chọn ảnh nào để tách")
    if len(moved) == len(files):
        raise ValueError("Đã chọn hết ảnh: SKU cũ phải còn lại ít nhất một ảnh")
    return moved


def next_id(engine, product_id: str) -> tuple[str, str]:
    """(mã sẽ cấp, thư mục gallery của SKU cũ) theo một catalog; lỗi nếu SKU cũ không tách được."""
    from engine.catalog.db import META_NEXT_PRODUCT_ID, Product, Session, get_meta

    with Session(engine) as session:
        product = session.get(Product, product_id)
        if product is None or not product.is_active or not product.gallery_folder:
            raise ValueError(f"SKU {product_id} không có, đã ngừng, hoặc không có thư mục gallery")
        new_id = get_meta(session, META_NEXT_PRODUCT_ID)
        if new_id is None:
            raise ValueError("Catalog chưa có next_product_id (chưa chạy migrate)")
        if session.get(Product, new_id) is not None:
            raise ValueError(f"Mã {new_id} đã có trong catalog dù next_product_id trỏ tới nó")
        return new_id, product.gallery_folder


def write_split(engine, product_id: str, new_id: str, folder: str, name: str | None, moved: int, left: int) -> None:
    """Một transaction: thêm SKU mới, tăng next_product_id, sửa số ảnh của SKU cũ."""
    from engine.catalog.db import META_NEXT_PRODUCT_ID, Product, Session, now_iso, set_meta

    with Session(engine) as session:
        old = session.get(Product, product_id)
        old.image_count, old.updated_at = left, now_iso()
        session.add(old)
        session.add(
            Product(
                product_id=new_id,
                product_name=name or f"Chưa đặt tên (tách từ {product_id})",
                gallery_folder=folder,
                image_count=moved,
                needs_naming=name is None,
            )
        )
        set_meta(session, META_NEXT_PRODUCT_ID, str(int(new_id) + 1))
        session.commit()


def split(engines: dict[str, object], gallery_dir: Path, product_id: str, chosen: list[str], name: str | None, dry_run: bool = False) -> str:
    """Tách SKU `product_id` trên mọi catalog trong `engines` (tên -> engine); trả mã mới."""
    from engine.catalog.reconcile import id_folder_name

    seen = {label: next_id(engine, product_id) for label, engine in engines.items()}
    if len(set(seen.values())) != 1:
        raise ValueError(f"Các catalog không khớp nhau (mã sẽ cấp, thư mục): {seen}. Chưa đổi gì.")
    new_id, old_folder = next(iter(seen.values()))
    source, target = gallery_dir / old_folder, gallery_dir / id_folder_name(new_id)
    if not source.is_dir():
        raise ValueError(f"Không thấy thư mục gallery: {source}")
    files = gallery_images(source)
    moved = check_selection(files, chosen)
    if target.exists():
        raise ValueError(f"Thư mục đích đã tồn tại: {target}")
    print(f"SKU {product_id} ({old_folder}, {len(files)} ảnh) -> giữ {len(files) - len(moved)} ảnh; SKU mới {new_id} ({target.name}) nhận {len(moved)} ảnh: {', '.join(moved)}")
    if dry_run:
        print("--dry-run: chưa đổi gì.")
        return new_id

    target.mkdir()
    for file_name in moved:
        shutil.move(str(source / file_name), str(target / file_name))
    written: list[str] = []
    try:
        for label, engine in engines.items():
            write_split(engine, product_id, new_id, target.name, name, len(moved), len(files) - len(moved))
            written.append(label)
    except Exception:
        if not written:  # chưa catalog nào đổi: trả ảnh về như cũ
            for file_name in moved:
                shutil.move(str(target / file_name), str(source / file_name))
            target.rmdir()
            print("LỖI khi ghi catalog: đã trả ảnh về thư mục cũ, chưa đổi gì.", file=sys.stderr)
        else:
            print(f"LỖI: catalog {written} đã ghi nhưng catalog còn lại thì chưa; ảnh đã chuyển. Sửa lỗi rồi ghi tay catalog còn thiếu.", file=sys.stderr)
        raise
    return new_id


def pick(folder: Path, product_id: str) -> tuple[list[str], str | None] | None:
    """Cửa sổ chọn ảnh cần tách (và tên SKU mới); None nếu đóng mà không tách."""
    import tkinter as tk

    from PIL import Image, ImageOps, ImageTk

    files = gallery_images(folder)
    side = 190 if len(files) <= 18 else 130
    columns = 6 if len(files) > 8 else 4
    root = tk.Tk()
    root.title(f"Tách SKU {product_id}: chọn ảnh thuộc sản phẩm cần tách ra")
    grid = tk.Frame(root, padx=8, pady=8)
    grid.pack()
    chosen: set[str] = set()
    photos, cells = [], {}
    result: list[tuple[list[str], str | None]] = []

    def refresh() -> None:
        for file_name, cell in cells.items():
            cell.configure(bg="#ef4444" if file_name in chosen else "#dddddd")
        button.configure(text=f"Tách {len(chosen)} ảnh đã chọn sang SKU mới (còn lại {len(files) - len(chosen)})", state="normal" if 0 < len(chosen) < len(files) else "disabled")

    def toggle(file_name: str) -> None:
        chosen.symmetric_difference_update({file_name})
        refresh()

    for index, file_name in enumerate(files):
        with Image.open(folder / file_name) as opened:
            picture = ImageOps.exif_transpose(opened).convert("RGB")
        picture.thumbnail((side, side))
        photos.append(ImageTk.PhotoImage(picture))  # giữ tham chiếu, nếu không Tk bỏ ảnh
        cell = tk.Frame(grid, bg="#dddddd", padx=5, pady=5)
        cell.grid(row=index // columns, column=index % columns, padx=3, pady=3)
        for widget in (tk.Label(cell, image=photos[-1], bd=0), tk.Label(cell, text=file_name, bg="white")):
            widget.pack(fill="x")
            widget.bind("<Button-1>", lambda _event, f=file_name: toggle(f))
        cells[file_name] = cell

    bottom = tk.Frame(root, padx=8, pady=8)
    bottom.pack(fill="x")
    tk.Label(bottom, text="Bấm vào ảnh để chọn (viền đỏ = sẽ tách ra). Tên SKU mới (để trống thì đặt sau ở Admin):").pack(anchor="w")
    entry = tk.Entry(bottom, font=("Segoe UI", 11))
    entry.pack(fill="x", pady=4)

    def confirm() -> None:
        result.append((sorted(chosen), entry.get().strip() or None))
        root.destroy()

    button = tk.Button(bottom, command=confirm)
    button.pack(fill="x")
    refresh()
    root.mainloop()
    return result[0] if result else None


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Tách một SKU thành hai: chuyển một phần ảnh gallery sang SKU mới.")
    parser.add_argument("--product", required=True, help="mã SKU đang gộp nhầm hai sản phẩm")
    parser.add_argument("--move", nargs="+", metavar="FILE", help="tên các ảnh gallery chuyển sang SKU mới (bỏ trống thì mở cửa sổ chọn)")
    parser.add_argument("--name", help="tên SKU mới (bỏ trống: đặt sau ở Admin)")
    parser.add_argument("--config", type=Path, help="file config của pipeline (mặc định configs/config.yaml)")
    parser.add_argument("--no-web-db", action="store_true", help="chỉ sửa catalog của pipeline, bỏ qua catalog của web")
    parser.add_argument("--dry-run", action="store_true", help="chỉ in việc sẽ làm")
    args = parser.parse_args(argv)

    from engine.catalog.db import make_engine, make_engine_from_url
    from engine.core.config import build_config, load_config

    config = build_config(args.config) if args.config else load_config()
    if config.catalog.source != "sqlite":
        print("LỖI: script này cần catalog.source = sqlite trong config", file=sys.stderr)
        return 2
    gallery_dir = config.resolve_path(config.paths.gallery_dir)
    engines = {"pipeline (SQLite)": make_engine(config.resolve_path(config.catalog.db_path))}
    if not args.no_web_db:
        from app.core.config import get_settings
        from app.core.db import sync_database_url

        engines["web (Postgres)"] = make_engine_from_url(sync_database_url(get_settings().database_url))
    try:
        chosen, name = args.move, args.name
        if chosen is None:
            _, folder = next_id(engines["pipeline (SQLite)"], args.product)
            picked = pick(gallery_dir / folder, args.product)
            if picked is None:
                print("Đã đóng cửa sổ: chưa đổi gì.")
                return 0
            chosen, name = picked[0], picked[1] or args.name
        new_id = split(engines, gallery_dir, args.product, chosen, name, args.dry_run)
    except ValueError as exc:
        print(f"LỖI: {exc}", file=sys.stderr)
        return 2
    finally:
        for engine in engines.values():
            engine.dispose()
    if not args.dry_run:
        print(f"Xong: SKU mới {new_id}. Việc còn lại:")
        print("  1. lập lại index gallery (config có retrieval.build_gallery_index: true) và chạy scripts/build_color_signatures.py")
        print(f"  2. Admin > Sản phẩm: đặt tên/giá/bằng chứng cho SKU {new_id}")
        print(f"  3. nhãn benchmark và dòng đơn hàng cũ vẫn mang mã {args.product}: sửa chỗ nào thực ra là SKU {new_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
