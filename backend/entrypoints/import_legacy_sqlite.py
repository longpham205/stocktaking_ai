"""Copy a legacy web database (the SQLite `app.db` of web v1) into Postgres, once
(`make import-legacy DATA_DIR=data_demo`).

    python -m entrypoints.import_legacy_sqlite --sqlite data_demo/db/app.db
    python -m entrypoints.import_legacy_sqlite --sqlite data/db/app.db --database-url postgresql+asyncpg://...

The SQLite file is opened read-only and never changed. Everything is written in one transaction:
a failure leaves the target as it was. The target must be migrated (`make migrate`) and empty;
`--replace` empties it first (every web and catalog table), for a second try.

What changes on the way: text timestamps become timestamptz; JSON kept as text becomes JSONB; a
capture's `status` (success/error/timeout) becomes `job_status` (done/error) with a `job_error`;
its `detections_json` `{w, h, boxes}` becomes `detections` + `image_width`/`image_height`; image
paths become relative to MEDIA_DIR (`<order id>/<file>`). The photo files themselves are not
copied: copy the legacy `transactions/` folder to MEDIA_DIR.
"""

import argparse
import json
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from sqlalchemy import Connection, create_engine, insert, text

from app.core.config import get_settings
from app.core.db import sync_database_url
from app.modules.audit.models import ChangeLogRow
from app.modules.auth.models import ShiftRow, UserRow
from app.modules.captures.models import CaptureRow
from app.modules.catalog.models import ProductPriceRow
from app.modules.engine_config.models import ConfigOverrideRow
from app.modules.orders.models import OrderItemRow, OrderRow
from app.modules.pos_settings.models import SettingRow

# emptied by --replace, children first
WEB_TABLES = (
    "change_log, captures, order_items, orders, shifts, users, product_prices, product_stock, settings, "
    "config_overrides"
)
CATALOG_TABLES = "product_evidence, product, color_reference, catalog_meta"
# the engine's tables are copied column for column (same definition on both sides)
CATALOG_COPY = ("catalog_meta", "color_reference", "product", "product_evidence")
# tables whose ids are copied: their sequences must continue after the highest one
SEQUENCES = ("users", "shifts", "orders", "order_items", "captures", "change_log", "product_evidence")


class ImportRefused(RuntimeError):
    pass


def _time(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)  # the legacy web wrote UTC


def _json(value: str | None) -> Any:
    return None if value is None or value == "" else json.loads(value)


def _media_path(path: str | None) -> str | None:
    """`.../transactions/12/capture_ab.jpg` (or a relative `12/t_ab.jpg`) -> `12/capture_ab.jpg`."""
    if not path:
        return None
    parts = PurePosixPath(path.replace("\\", "/")).parts
    return "/".join(parts[-2:]) if len(parts) >= 2 else path


def _capture(row: sqlite3.Row) -> dict[str, Any]:
    detections = _json(row["detections_json"]) or {}
    status = row["status"]
    error = None
    if status != "success":
        code = "PIPELINE_TIMEOUT" if status == "timeout" else "PIPELINE_ERROR"
        error = {"code": code, "message": row["error"] or status}
    return {
        "id": row["id"],
        "order_id": row["order_id"],
        "job_status": "done" if status == "success" else "error",
        "job_error": error,
        "item_count": row["item_count"],
        "processing_time_ms": row["processing_time_ms"],
        "image_path": _media_path(row["image_path"]),
        "image_width": detections.get("w"),
        "image_height": detections.get("h"),
        "detections": detections.get("boxes"),
        "created_at": _time(row["created_at"]),
    }


def _tables(source: sqlite3.Connection) -> set[str]:
    return {row[0] for row in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _rows(source: sqlite3.Connection, table: str) -> list[sqlite3.Row]:
    return source.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()  # noqa: S608  fixed table names


def _copy(conn: Connection, model: Any, rows: list[dict[str, Any]]) -> int:
    if rows:
        conn.execute(insert(model), rows)
    return len(rows)


def import_legacy(sqlite_path: Path, sync_url: str, replace: bool = False) -> dict[str, int]:
    """Copy every table. Returns how many rows each received."""
    if not sqlite_path.is_file():
        raise FileNotFoundError(f"Không thấy file SQLite: {sqlite_path}")
    source = sqlite3.connect(f"file:{sqlite_path.as_posix()}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    engine = create_engine(sync_url)
    counts: dict[str, int] = {}
    try:
        present = _tables(source)
        with engine.begin() as conn:
            if replace:
                conn.execute(text(f"TRUNCATE {WEB_TABLES}, {CATALOG_TABLES} RESTART IDENTITY CASCADE"))
            else:
                for table in ("users", "orders", "product"):
                    if conn.execute(text(f"SELECT 1 FROM {table} LIMIT 1")).first():  # noqa: S608
                        raise ImportRefused(
                            f"Cơ sở dữ liệu đích đã có dữ liệu (bảng {table}). Dùng một database trống, "
                            "hoặc thêm --replace để XOÁ dữ liệu đích rồi nhập lại."
                        )

            for table in CATALOG_COPY:
                if table not in present:
                    continue
                rows = [dict(row) for row in _rows(source, table)]
                for row in rows:  # SQLite keeps booleans as 0/1
                    for key in ("is_active", "needs_naming"):
                        if key in row:
                            row[key] = bool(row[key])
                if rows:
                    columns = ", ".join(rows[0])
                    values = ", ".join(f":{key}" for key in rows[0])
                    conn.execute(text(f"INSERT INTO {table} ({columns}) VALUES ({values})"), rows)  # noqa: S608
                counts[table] = len(rows)

            users = _rows(source, "users")
            counts["users"] = _copy(
                conn,
                UserRow,
                [
                    {
                        "id": r["id"],
                        "username": r["username"],
                        "password_hash": r["password_hash"],  # scrypt, same format: passwords keep working
                        "full_name": r["full_name"],
                        "role": r["role"],
                        "is_active": bool(r["is_active"]),
                        "has_seen_onboarding": bool(r["has_seen_onboarding"]),
                        "created_at": _time(r["created_at"]),
                    }
                    for r in users
                ],
            )
            user_ids = {r["id"] for r in users}
            counts["shifts"] = _copy(
                conn,
                ShiftRow,
                [
                    {
                        "id": r["id"],
                        "user_id": r["user_id"],
                        "started_at": _time(r["started_at"]),
                        # nobody is logged in to the new system yet: every imported shift is closed
                        "ended_at": _time(r["ended_at"]) or _time(r["started_at"]),
                        "total_collected": r["total_collected"],
                    }
                    for r in _rows(source, "shifts")
                ],
            )
            counts["orders"] = _copy(
                conn,
                OrderRow,
                [
                    {
                        "id": r["id"],
                        "shift_id": r["shift_id"],
                        "cashier_id": r["cashier_id"],
                        "status": r["status"],
                        "payment_method": r["payment_method"],
                        "cash_given": r["cash_given"],
                        "change_given": r["change_given"],
                        "total_amount": r["total_amount"],
                        "created_at": _time(r["created_at"]),
                        "paid_at": _time(r["paid_at"]),
                    }
                    for r in _rows(source, "orders")
                ],
            )
            counts["order_items"] = _copy(
                conn,
                OrderItemRow,
                [
                    {
                        "id": r["id"],
                        "order_id": r["order_id"],
                        "product_id": r["product_id"],
                        "quantity": r["quantity"],
                        "unit_price": r["unit_price"],
                        "manual_price": r["manual_price"],
                        "flagged": bool(r["flagged"]),
                        "thumb_path": _media_path(r["thumb_path"]),
                        "evidence": _json(r["evidence_json"]),
                        "created_at": _time(r["created_at"]),
                    }
                    for r in _rows(source, "order_items")
                ],
            )
            counts["captures"] = _copy(conn, CaptureRow, [_capture(r) for r in _rows(source, "captures")])
            counts["product_prices"] = _copy(
                conn,
                ProductPriceRow,
                [
                    {"product_id": r["product_id"], "price": r["price"], "updated_at": _time(r["updated_at"])}
                    for r in _rows(source, "product_prices")
                ],
            )
            counts["settings"] = _copy(
                conn, SettingRow, [{"key": r["key"], "value": _json(r["value"])} for r in _rows(source, "settings")]
            )
            if "config_overrides" in present:
                counts["config_overrides"] = _copy(
                    conn,
                    ConfigOverrideRow,
                    [
                        {
                            "key": r["key"],
                            "value": _json(r["value_json"]),
                            "updated_by": r["updated_by"],
                            "updated_at": _time(r["updated_at"]),
                        }
                        for r in _rows(source, "config_overrides")
                    ],
                )
            counts["change_log"] = _copy(
                conn,
                ChangeLogRow,
                [
                    {
                        "id": r["id"],
                        "table_name": r["table_name"],
                        "record_id": r["record_id"],
                        "field_name": r["field_name"],
                        "old_value": r["old_value"],
                        "new_value": r["new_value"],
                        # an entry by an account that no longer exists keeps its change, not its author
                        "changed_by": r["changed_by"] if r["changed_by"] in user_ids else None,
                        "changed_at": _time(r["changed_at"]),
                    }
                    for r in _rows(source, "change_log")
                ],
            )
            for table in SEQUENCES:  # ids were copied: the next generated id must come after them
                conn.execute(
                    text(
                        f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "  # noqa: S608
                        f"COALESCE((SELECT max(id) FROM {table}), 0) + 1, false)"
                    )
                )
    finally:
        source.close()
        engine.dispose()
    return counts


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Nhập cơ sở dữ liệu SQLite của web cũ vào Postgres (một lần).")
    parser.add_argument("--sqlite", type=Path, required=True, help="file app.db của web cũ (chỉ đọc)")
    parser.add_argument("--database-url", help="đích (postgresql+asyncpg://...); mặc định: DATABASE_URL")
    parser.add_argument("--replace", action="store_true", help="XOÁ mọi dữ liệu web + catalog ở đích trước khi nhập")
    args = parser.parse_args(argv)
    target = sync_database_url(args.database_url or get_settings().database_url)
    try:
        counts = import_legacy(args.sqlite, target, replace=args.replace)
    except (ImportRefused, FileNotFoundError) as exc:
        print(f"LỖI: {exc}", file=sys.stderr)
        return 2
    for table, count in counts.items():
        print(f"{table}: {count}")
    print("Ảnh chụp và ảnh thu nhỏ không được chép: chép thư mục transactions/ cũ vào MEDIA_DIR.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
