"""SQLite (WAL): schema + tiện ích giao dịch. Mỗi lần dùng mở một kết nối riêng (an toàn đa luồng)."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL,
  full_name TEXT NOT NULL DEFAULT '', role TEXT NOT NULL DEFAULT 'staff',
  is_active INTEGER NOT NULL DEFAULT 1, has_seen_onboarding INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS shifts(
  id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
  started_at TEXT NOT NULL, ended_at TEXT, total_collected INTEGER NOT NULL DEFAULT 0);
CREATE UNIQUE INDEX IF NOT EXISTS ux_shift_open ON shifts(user_id) WHERE ended_at IS NULL;
CREATE TABLE IF NOT EXISTS orders(
  id INTEGER PRIMARY KEY, shift_id INTEGER REFERENCES shifts(id), cashier_id INTEGER NOT NULL REFERENCES users(id),
  status TEXT NOT NULL DEFAULT 'open', payment_method TEXT, cash_given INTEGER, change_given INTEGER,
  total_amount INTEGER, created_at TEXT NOT NULL, paid_at TEXT);
CREATE INDEX IF NOT EXISTS ix_orders_cashier ON orders(cashier_id, created_at);
CREATE TABLE IF NOT EXISTS order_items(
  id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
  product_id TEXT NOT NULL, quantity INTEGER NOT NULL DEFAULT 1, unit_price INTEGER, manual_price INTEGER,
  flagged INTEGER NOT NULL DEFAULT 0, thumb_path TEXT, evidence_json TEXT, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_items_order ON order_items(order_id);
CREATE TABLE IF NOT EXISTS captures(
  id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL REFERENCES orders(id), status TEXT NOT NULL,
  item_count INTEGER NOT NULL DEFAULT 0, processing_time_ms REAL, image_path TEXT, error TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS product_prices(product_id TEXT PRIMARY KEY, price INTEGER NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS product_overrides(product_id TEXT PRIMARY KEY, barcode TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS ux_override_barcode ON product_overrides(barcode) WHERE barcode <> '';
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS change_log(
  id INTEGER PRIMARY KEY, table_name TEXT NOT NULL, record_id TEXT NOT NULL, field_name TEXT NOT NULL,
  old_value TEXT, new_value TEXT, changed_by INTEGER, changed_at TEXT NOT NULL);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = self._open()
        try:
            conn.execute("PRAGMA journal_mode=WAL")  # ngoài giao dịch
            conn.executescript(SCHEMA)
        finally:
            conn.close()

    def _open(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def backup(self, dest: Path) -> Path:
        """Sao lưu nhất quán bằng SQLite backup API (an toàn khi server đang chạy, không bỏ sót -wal)."""
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        src, dst = self._open(), sqlite3.connect(str(dest))
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        return dest

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """Giao dịch ghi (BEGIN IMMEDIATE); tự rollback khi lỗi."""
        conn = self._open()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.execute("COMMIT")
        except BaseException:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        finally:
            conn.close()

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        conn = self._open()
        try:
            yield conn
        finally:
            conn.close()
