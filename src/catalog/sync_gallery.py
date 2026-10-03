"""Đồng bộ thư mục gallery vào catalog SQLite (phía GHI).

- Thư mục mới trong gallery -> tạo ``Product`` (ID từ ``CatalogMeta.next_product_id``, tăng trong
  cùng transaction; tên tạm; ``needs_naming=true``). ID đã cấp không bao giờ cấp lại.
- Thư mục biến mất -> chỉ cảnh báo, KHÔNG xoá SKU.
- Cập nhật ``image_count``.
- ``import_inbox``: mỗi thư mục trong ``gallery_inbox`` được cấp ID rồi chuyển thành
  ``<gallery>/<ID 4 chữ số>`` (quy ước đặt tên mới).

CLI:
    python -m src.catalog.sync_gallery --db data/db/app.db --gallery-dir data/gallery [--inbox-dir data/gallery_inbox] [--dry-run]
"""

from __future__ import annotations

import argparse
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

from src.catalog.reconcile import ReconcilePlan, id_folder_name, reconcile
from src.core.logger import get_logger
from src.core.utils import list_image_files

logger = get_logger(__name__)


@dataclass
class SyncResult:
    plan: ReconcilePlan
    created: dict[str, str] = field(default_factory=dict)   # product_id -> thư mục
    imported: dict[str, str] = field(default_factory=dict)  # thư mục inbox -> product_id


def scan_gallery(gallery_dir: Path) -> dict[str, int]:
    return {d.name: len(list_image_files(d)) for d in sorted(Path(gallery_dir).iterdir()) if d.is_dir()}


def _allocate_id(session) -> str:
    from src.catalog.db import META_NEXT_PRODUCT_ID, get_meta, set_meta

    current = get_meta(session, META_NEXT_PRODUCT_ID)
    if current is None:
        raise RuntimeError("Catalog chưa có next_product_id (chưa chạy migrate).")
    set_meta(session, META_NEXT_PRODUCT_ID, str(int(current) + 1))
    return current


def sync_gallery(engine, gallery_dir: Path, inbox_dir: Path | None = None, dry_run: bool = False) -> SyncResult:
    from src.catalog.db import Product, Session, create_all, now_iso, select

    gallery_dir = Path(gallery_dir)
    if not gallery_dir.is_dir():
        raise FileNotFoundError(f"Không thấy thư mục gallery: {gallery_dir}")
    create_all(engine)
    result_imported: dict[str, str] = {}

    with Session(engine) as s:
        rows = s.exec(select(Product)).all()
        catalog_folders = {r.gallery_folder: r.product_id for r in rows if r.gallery_folder}
        counts = {r.product_id: r.image_count for r in rows}

        # 1. Nhập thư mục từ inbox: cấp ID, chuyển vào gallery với tên chuẩn.
        if inbox_dir is not None and Path(inbox_dir).is_dir():
            for src in sorted(d for d in Path(inbox_dir).iterdir() if d.is_dir()):
                if not list_image_files(src):
                    logger.warning("Bỏ qua thư mục inbox không có ảnh: '%s'", src.name)
                    continue
                if dry_run:
                    result_imported[src.name] = "(chưa cấp)"
                    continue
                pid = _allocate_id(s)
                dest = gallery_dir / id_folder_name(pid)
                if dest.exists():
                    raise RuntimeError(f"Thư mục đích đã tồn tại: {dest}")
                s.add(Product(product_id=pid, product_name=f"Chưa đặt tên ({src.name})", gallery_folder=dest.name,
                              image_count=len(list_image_files(src)), needs_naming=True))
                s.commit()  # ghi DB trước; nếu chuyển thư mục lỗi, lần sync sau sẽ báo "thư mục mất"
                shutil.move(str(src), str(dest))
                catalog_folders[dest.name] = pid
                counts[pid] = len(list_image_files(dest))
                result_imported[src.name] = pid
                logger.info("Nhập SKU mới từ inbox: '%s' -> ID %s (%s)", src.name, pid, dest.name)

        # 2. So khớp gallery hiện tại.
        plan = reconcile(catalog_folders, counts, scan_gallery(gallery_dir))
        for pid, folder in plan.missing_folders.items():
            logger.warning("SKU %s: thư mục gallery '%s' không còn trên đĩa (không xoá SKU).", pid, folder)

        created: dict[str, str] = {}
        if not dry_run:
            for folder in plan.new_folders:
                pid = _allocate_id(s)
                s.add(Product(product_id=pid, product_name=f"Chưa đặt tên ({folder})", gallery_folder=folder,
                              image_count=len(list_image_files(gallery_dir / folder)), needs_naming=True))
                created[pid] = folder
                logger.info("SKU mới từ gallery: '%s' -> ID %s", folder, pid)
            for pid, n in plan.image_count_updates.items():
                row = s.get(Product, pid)
                row.image_count = n
                row.updated_at = now_iso()
                s.add(row)
            s.commit()
        else:
            s.rollback()
    return SyncResult(plan=plan, created=created, imported=result_imported)


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Đồng bộ thư mục gallery vào catalog SQLite.")
    ap.add_argument("--db", required=True, type=Path)
    ap.add_argument("--gallery-dir", required=True, type=Path)
    ap.add_argument("--inbox-dir", type=Path, help="Thư mục chứa ảnh SKU mới chưa có ID")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    if not args.db.is_file():
        print(f"Không thấy DB catalog: {args.db} (chạy migrate trước).", file=sys.stderr)
        return 2

    from src.catalog.db import make_engine

    engine = make_engine(args.db)
    try:
        res = sync_gallery(engine, args.gallery_dir, args.inbox_dir, dry_run=args.dry_run)
    finally:
        engine.dispose()
    p = res.plan
    print(f"Inbox nhập: {res.imported or '-'}")
    print(f"Thư mục mới: {p.new_folders or '-'} | tạo SKU: {res.created or '-'}")
    print(f"Cập nhật số ảnh: {p.image_count_updates or '-'} | thư mục mất: {p.missing_folders or '-'}")
    if args.dry_run:
        print("(dry-run: không ghi gì)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
