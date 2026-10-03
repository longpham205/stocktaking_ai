"""Xuất catalog từ DB SQLite ra ``catalog_snapshot.json`` (dùng cho Colab/Kaggle, ``catalog.source: snapshot``).

Ví dụ:
    python scripts/export_catalog_snapshot.py --db data/db/app.db --out data/metadata/catalog_snapshot.json
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.catalog.repository import SqliteCatalogRepository  # noqa: E402
from src.catalog.snapshot import JsonSnapshotCatalogRepository, write_snapshot  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Xuất catalog SQLite ra snapshot JSON.")
    parser.add_argument("--db", required=True, type=Path, help="Đường dẫn app.db")
    parser.add_argument("--out", required=True, type=Path, help="File snapshot JSON đầu ra")
    args = parser.parse_args(argv)
    # Windows: stdout bị chuyển hướng dùng cp1252 -> in tiếng Việt sẽ lỗi.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    repo = SqliteCatalogRepository(args.db)
    out = write_snapshot(repo.data, args.out)
    # Đọc lại để chắc chắn file xuất ra nạp được và giống hệt nguồn.
    check = JsonSnapshotCatalogRepository(out)
    if check.version() != repo.version():
        print(f"LỖI: snapshot đọc lại có version {check.version()} khác DB {repo.version()}.", file=sys.stderr)
        return 1
    print(f"Đã xuất {len(repo.products())} SKU -> {out} (version {repo.version()})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
