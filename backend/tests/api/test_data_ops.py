"""The data commands of phase 7: import of a web v1 SQLite database, demo prices, media purge."""

import json
import os
import sqlite3
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from sqlalchemy import create_engine, text
from sqlmodel import Session

from app.core.db import sync_database_url
from engine.catalog.db import Product, make_engine_from_url
from entrypoints.import_legacy_sqlite import ImportRefused, import_legacy
from entrypoints.purge_media import purge
from entrypoints.seed_demo import seed_prices
from tests.api.conftest import http, login

# the tables of web v1 (backend/db.py at git commit f30710d) and of its catalog (the engine's, on SQLite)
LEGACY_SCHEMA = """
CREATE TABLE users(id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL,
  full_name TEXT NOT NULL DEFAULT '', role TEXT NOT NULL DEFAULT 'staff', is_active INTEGER NOT NULL DEFAULT 1,
  has_seen_onboarding INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
CREATE TABLE shifts(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, started_at TEXT NOT NULL, ended_at TEXT,
  total_collected INTEGER NOT NULL DEFAULT 0);
CREATE TABLE orders(id INTEGER PRIMARY KEY, shift_id INTEGER, cashier_id INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'open',
  payment_method TEXT, cash_given INTEGER, change_given INTEGER, total_amount INTEGER, created_at TEXT NOT NULL, paid_at TEXT);
CREATE TABLE order_items(id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL, product_id TEXT NOT NULL,
  quantity INTEGER NOT NULL DEFAULT 1, unit_price INTEGER, manual_price INTEGER, flagged INTEGER NOT NULL DEFAULT 0,
  thumb_path TEXT, evidence_json TEXT, created_at TEXT NOT NULL);
CREATE TABLE captures(id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL, status TEXT NOT NULL, item_count INTEGER NOT NULL DEFAULT 0,
  processing_time_ms REAL, image_path TEXT, error TEXT, created_at TEXT NOT NULL, detections_json TEXT);
CREATE TABLE product_prices(product_id TEXT PRIMARY KEY, price INTEGER NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE config_overrides(key TEXT PRIMARY KEY, value_json TEXT NOT NULL, updated_by TEXT, updated_at TEXT NOT NULL);
CREATE TABLE change_log(id INTEGER PRIMARY KEY, table_name TEXT NOT NULL, record_id TEXT NOT NULL, field_name TEXT NOT NULL,
  old_value TEXT, new_value TEXT, changed_by INTEGER, changed_at TEXT NOT NULL);
CREATE TABLE product(product_id TEXT PRIMARY KEY, product_name TEXT NOT NULL, brand TEXT, category TEXT, barcode TEXT,
  description TEXT, image_count INTEGER NOT NULL DEFAULT 0, gallery_folder TEXT, is_active INTEGER NOT NULL DEFAULT 1,
  needs_naming INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE product_evidence(id INTEGER PRIMARY KEY, product_id TEXT NOT NULL, evidence_type TEXT NOT NULL,
  value_json TEXT NOT NULL, updated_by TEXT, updated_at TEXT NOT NULL);
CREATE TABLE color_reference(color_code TEXT PRIMARY KEY, r INTEGER NOT NULL, g INTEGER NOT NULL, b INTEGER NOT NULL,
  hex TEXT NOT NULL, source TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE catalog_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""
T = "2026-09-30T08:00:00+00:00"
STAFF_PASSWORD = "staff-pass-123"


@pytest.fixture
def legacy_db(tmp_path: Path) -> Path:
    from app.modules.auth.passwords import hash_password

    path = tmp_path / "app.db"
    db = sqlite3.connect(path)
    db.executescript(LEGACY_SCHEMA)
    db.execute("INSERT INTO users VALUES(1,'staff',?,'Thu ngân cũ','staff',1,1,?)", (hash_password(STAFF_PASSWORD), T))
    db.execute("INSERT INTO users VALUES(2,'admin','x','Quản trị','admin',1,0,?)", (T,))
    db.execute("INSERT INTO shifts VALUES(1,1,?,NULL,60000)", (T,))  # left open by the old server
    db.execute("INSERT INTO orders VALUES(1,1,1,'paid','cash',100000,40000,60000,?,?)", (T, T))
    db.execute("INSERT INTO orders VALUES(2,1,1,'open',NULL,NULL,NULL,NULL,?,NULL)", (T,))
    db.execute(
        "INSERT INTO order_items VALUES(1,1,'7',2,30000,NULL,0,'1/t_ab.jpg',?,?)",
        (json.dumps({"ocr": {"text": "ABA"}}), T),
    )
    db.execute("INSERT INTO order_items VALUES(2,2,'8',1,NULL,5000,1,NULL,NULL,?)", (T,))
    boxes = [{"item_id": 1, "product_id": "7", "status": "accepted", "bbox": [1, 2, 3, 4]}]
    db.execute(
        "INSERT INTO captures VALUES(1,1,'success',2,812.5,'C:\\old\\data\\transactions\\1\\capture_ab.jpg',NULL,?,?)",
        (T, json.dumps({"w": 320, "h": 240, "boxes": boxes})),
    )
    db.execute(
        "INSERT INTO captures VALUES(2,2,'timeout',0,60000.0,'/old/transactions/2/capture_cd.jpg','timeout',?,NULL)",
        (T,),
    )
    db.execute("INSERT INTO product_prices VALUES('7',30000,?)", (T,))
    db.execute("INSERT INTO settings VALUES('similarity_threshold','0.7')")
    db.execute("INSERT INTO settings VALUES('advanced_password_hash','\"scrypt$abc\"')")
    db.execute("INSERT INTO config_overrides VALUES('retrieval.top_k','6','admin',?)", (T,))
    db.execute("INSERT INTO change_log VALUES(1,'product','7','price',NULL,'30000',2,?)", (T,))
    db.execute("INSERT INTO change_log VALUES(2,'product','7','barcode','','893',99,?)", (T,))  # author deleted since
    db.execute("INSERT INTO product VALUES('7','Kem ABA',NULL,NULL,'893',NULL,3,'kem_aba',1,0,?,?)", (T, T))
    db.execute("INSERT INTO product VALUES('8','SKU 8',NULL,NULL,NULL,NULL,0,NULL,0,1,?,?)", (T, T))
    db.execute("INSERT INTO product_evidence VALUES(5,'7','ocr_keywords','[\"ABA\"]','admin',?)", (T,))
    db.execute("INSERT INTO color_reference VALUES('BE203',109,63,62,'#6D3F3E','seed',?)", (T,))
    db.execute("INSERT INTO catalog_meta VALUES('next_product_id','9')")
    db.commit()
    db.close()
    return path


async def test_import_copies_everything_and_the_app_works_on_it(
    database_url: str, catalog_url: str, legacy_db: Path, db_app: FastAPI
) -> None:
    before = legacy_db.read_bytes()
    with pytest.raises(ImportRefused):  # the app fixture seeded accounts: not empty
        import_legacy(legacy_db, catalog_url)
    counts = import_legacy(legacy_db, catalog_url, replace=True)
    assert legacy_db.read_bytes() == before  # the source is only read
    assert counts == {
        "catalog_meta": 1,
        "color_reference": 1,
        "product": 2,
        "product_evidence": 1,
        "users": 2,
        "shifts": 1,
        "orders": 2,
        "order_items": 2,
        "captures": 2,
        "product_prices": 1,
        "settings": 2,
        "config_overrides": 1,
        "change_log": 2,
    }
    engine = create_engine(catalog_url)
    with engine.connect() as conn:
        capture = conn.execute(text("SELECT * FROM captures WHERE id = 1")).mappings().one()
        assert (capture["job_status"], capture["image_path"]) == ("done", "1/capture_ab.jpg")
        assert (capture["image_width"], capture["image_height"]) == (320, 240) and capture["detections"][0][
            "item_id"
        ] == 1
        timed_out = conn.execute(text("SELECT job_status, job_error FROM captures WHERE id = 2")).one()
        assert timed_out == ("error", {"code": "PIPELINE_TIMEOUT", "message": "timeout"})
        assert conn.execute(text("SELECT ended_at IS NOT NULL FROM shifts")).scalar_one()  # nobody is logged in yet
        assert conn.execute(text("SELECT changed_by FROM change_log ORDER BY id")).scalars().all() == [2, None]
        assert conn.execute(text("SELECT value FROM config_overrides")).scalar_one() == 6
        assert conn.execute(text("SELECT is_active, needs_naming FROM product WHERE product_id = '8'")).one() == (
            False,
            True,
        )
    engine.dispose()

    # the imported cashier logs in with the old password and finds the old order, priced and named
    response = await login(db_app, "staff", STAFF_PASSWORD)
    assert response.status_code == 200
    async with http(db_app, response.json()["token"]) as staff:
        assert (await staff.get("/api/settings")).json()["similarity_threshold"] == 0.7
        resumed = (await staff.get("/api/orders/open")).json()["order"]
        assert (
            resumed["id"] == 2
            and resumed["items"][0]["manual_price"] is True
            and resumed["items"][0]["flagged"] is True
        )
        paid = (await staff.get("/api/orders/1")).json()
        assert paid["total"] == 60000 and paid["items"][0]["product_name"] == "Kem ABA"
        assert paid["items"][0]["evidence"] == {"ocr": {"text": "ABA"}}
        assert paid["items"][0]["thumbnail_url"].startswith("/api/media/1/t_ab.jpg?")
        # ids continue after the imported ones
        assert (await staff.post("/api/orders/2/items", json={"product_id": "7"})).json()["items"][-1]["id"] == 3


def test_seed_prices_keeps_existing_prices_and_skips_unknown_products(
    catalog_url: str, database_url: str, tmp_path: Path
) -> None:
    engine = make_engine_from_url(catalog_url)
    with Session(engine) as session:
        session.add(Product(product_id="1", product_name="A"))
        session.add(Product(product_id="2", product_name="B"))
        session.commit()
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO product_prices (product_id, price) VALUES ('2', 999)"))
    engine.dispose()
    prices = tmp_path / "product_prices.json"
    prices.write_text(json.dumps({"1": 174000, "2": 133000, "8": None, "77": 5000}), encoding="utf-8")
    assert sync_database_url(database_url) == catalog_url
    assert seed_prices(catalog_url, prices) == (1, 3)
    assert seed_prices(catalog_url, prices) == (0, 4)  # again: nothing new
    engine = create_engine(catalog_url)
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT product_id, price FROM product_prices ORDER BY product_id")).all()
    engine.dispose()
    assert [tuple(row) for row in rows] == [("1", 174000), ("2", 999)]  # the admin's price is kept


def test_purge_media_removes_only_old_order_folders(tmp_path: Path) -> None:
    now = time.time()
    for name, age_days in (("1", 45), ("2", 5)):
        folder = tmp_path / name
        folder.mkdir()
        (folder / "capture.jpg").write_bytes(b"x")
        os.utime(folder, (now - age_days * 86400, now - age_days * 86400))
    assert [p.name for p in purge(tmp_path, 30, dry_run=True, now=now)] == ["1"]
    assert (tmp_path / "1").exists()  # a dry run deletes nothing
    assert [p.name for p in purge(tmp_path, 30, now=now)] == ["1"]
    assert not (tmp_path / "1").exists() and (tmp_path / "2" / "capture.jpg").exists()
    assert purge(tmp_path / "missing", 30) == []
