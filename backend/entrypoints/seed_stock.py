"""Give every product on sale that has no stock yet a random quantity on hand (`make seed-stock`),
so the stock screens have numbers before anyone has counted the shelves.

    python -m entrypoints.seed_stock                     # 20..100 each, into DATABASE_URL
    python -m entrypoints.seed_stock --min 5 --max 30

Safe to run again: a product that already has a quantity is left alone (an admin may have counted
it, sales may have moved it).
"""

import argparse
import random
import sys

from sqlalchemy import create_engine, text

from app.core.config import get_settings
from app.core.db import sync_database_url


def seed_stock(sync_url: str, low: int, high: int, rng: random.Random | None = None) -> int:
    """Insert a quantity between `low` and `high` for each product on sale without one. Returns how many."""
    rng = rng or random.Random()  # noqa: S311  demo numbers, not a secret
    inserted = 0
    engine = create_engine(sync_url)
    try:
        with engine.begin() as conn:
            untracked = conn.execute(
                text(
                    "SELECT p.product_id FROM product p LEFT JOIN product_stock s ON s.product_id = p.product_id "
                    "WHERE p.is_active AND s.product_id IS NULL ORDER BY p.product_id"
                )
            ).scalars()
            for product_id in list(untracked):
                done = conn.execute(
                    text(
                        "INSERT INTO product_stock (product_id, quantity) VALUES (:id, :quantity) "
                        "ON CONFLICT (product_id) DO NOTHING"
                    ),
                    {"id": product_id, "quantity": rng.randint(low, high)},
                )
                inserted += done.rowcount
    finally:
        engine.dispose()
    return inserted


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Gán tồn kho ngẫu nhiên cho sản phẩm chưa có số tồn.")
    parser.add_argument("--min", type=int, default=20, dest="low", help="số tồn nhỏ nhất (mặc định 20)")
    parser.add_argument("--max", type=int, default=100, dest="high", help="số tồn lớn nhất (mặc định 100)")
    args = parser.parse_args(argv)
    if not 0 <= args.low <= args.high:
        parser.error("cần 0 <= --min <= --max")
    inserted = seed_stock(sync_database_url(get_settings().database_url), args.low, args.high)
    print(
        f"Tồn kho: gán số ngẫu nhiên {args.low}..{args.high} cho {inserted} sản phẩm (sản phẩm đã có số tồn giữ nguyên)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
