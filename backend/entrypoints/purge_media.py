"""Delete capture photos older than the retention period (`make purge-media DAYS=30`).

    python -m entrypoints.purge_media --days 30            # delete
    python -m entrypoints.purge_media --days 30 --dry-run  # only say what would go

The legacy web did this at every start; here it is an explicit command (a cron job in production).
An order's photos live in `MEDIA_DIR/<order id>/`; a folder untouched for longer than the period is
removed whole. The order itself stays: its lines keep their names and prices, only the pictures go
(the order view then simply shows no photo).
"""

import argparse
import shutil
import sys
import time
from pathlib import Path

from app.core.config import get_settings


def purge(media_dir: Path, days: float, dry_run: bool = False, now: float | None = None) -> list[Path]:
    """Remove the order folders not modified for `days`. Returns the folders (to be) removed."""
    if not media_dir.is_dir():
        return []
    cutoff = (time.time() if now is None else now) - days * 86400
    removed = []
    for folder in sorted(media_dir.iterdir()):
        if folder.is_dir() and folder.stat().st_mtime < cutoff:
            removed.append(folder)
            if not dry_run:
                shutil.rmtree(folder)
    return removed


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Xoá ảnh chụp cũ hơn hạn lưu trữ.")
    parser.add_argument("--days", type=float, default=30, help="hạn lưu trữ (ngày)")
    parser.add_argument("--dry-run", action="store_true", help="chỉ liệt kê, không xoá")
    args = parser.parse_args(argv)
    if args.days <= 0:
        print("LỖI: --days phải lớn hơn 0", file=sys.stderr)
        return 2
    media_dir = get_settings().media_dir
    removed = purge(media_dir, args.days, dry_run=args.dry_run)
    verb = "Sẽ xoá" if args.dry_run else "Đã xoá"
    print(f"{verb} {len(removed)} thư mục ảnh cũ hơn {args.days:g} ngày trong {media_dir}")
    for folder in removed:
        print(f"  {folder.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
