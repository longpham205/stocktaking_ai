"""Fill the database with the demo catalog and its prices (`make seed-demo`), so a fresh
`make docker-up` has products to sell.

    python -m entrypoints.seed_demo                      # data_demo/, into DATABASE_URL

Safe to run again: the engine's catalog migrate only inserts what is missing, and a price that is
already set is left alone (an admin may have changed it).
"""

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import create_engine, text

from app.core.config import BACKEND_DIR, get_settings
from app.core.db import sync_database_url


def seed_prices(sync_url: str, prices_file: Path) -> tuple[int, int]:
    """Insert the seed prices of products that exist and have none. Returns (inserted, skipped)."""
    prices = json.loads(prices_file.read_text(encoding="utf-8"))
    inserted = skipped = 0
    engine = create_engine(sync_url)
    try:
        with engine.begin() as conn:
            known = set(conn.execute(text("SELECT product_id FROM product")).scalars())
            for product_id, price in prices.items():
                if price is None or product_id not in known:
                    skipped += 1
                    continue
                done = conn.execute(
                    text(
                        "INSERT INTO product_prices (product_id, price) VALUES (:id, :price) "
                        "ON CONFLICT (product_id) DO NOTHING"
                    ),
                    {"id": product_id, "price": int(price)},
                )
                inserted += done.rowcount
                skipped += 1 - done.rowcount
    finally:
        engine.dispose()
    return inserted, skipped


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Nạp catalog demo và giá demo vào cơ sở dữ liệu.")
    parser.add_argument("--data-dir", type=Path, default=BACKEND_DIR / "data_demo", help="thư mục dữ liệu demo")
    parser.add_argument(
        "--legacy-config", type=Path, default=BACKEND_DIR / "configs" / "legacy" / "config.demo.legacy.yaml"
    )
    args = parser.parse_args(argv)
    sync_url = sync_database_url(get_settings().database_url)

    # the catalog is the engine's: its own migrate builds it (names, folders, evidence, colours)
    from engine.catalog.migrate import main as migrate_catalog

    code: int = migrate_catalog(
        [
            "--seed-dir",
            str(args.data_dir / "seed"),
            "--legacy-config",
            str(args.legacy_config),
            "--db-url",
            sync_url,
            "--gallery-dir",
            str(args.data_dir / "gallery"),
            "--benchmark-labels",
            str(args.data_dir / "benchmark" / "_annotations.coco.json"),
        ]
    )
    if code != 0:
        return code
    inserted, skipped = seed_prices(sync_url, args.data_dir / "seed" / "product_prices.json")
    print(
        f"Giá demo: chèn {inserted}, bỏ qua {skipped} (đã có giá, không có giá seed, hoặc SKU không có trong catalog)"
    )
    print("Để ảnh gallery và pipeline demo khớp catalog này: PIPELINE_CONFIG=configs/config.demo.yaml")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
