"""Test schema catalog (src/catalog/db.py)."""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError

from src.catalog.db import (
    SCHEMA_VERSION,
    CatalogMeta,
    ColorReference,
    Product,
    ProductEvidence,
    Session,
    create_all,
    get_meta,
    make_engine,
    select,
    set_meta,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def engine(tmp_path):
    eng = make_engine(tmp_path / "db" / "app.db")
    create_all(eng)
    yield eng
    eng.dispose()


def _tables(path: Path) -> set[str]:
    con = sqlite3.connect(path)
    try:
        return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        con.close()


def test_create_all_idempotent_and_writes_schema_version(tmp_path):
    db = tmp_path / "db" / "app.db"
    eng = make_engine(db)
    create_all(eng)
    create_all(eng)
    with Session(eng) as s:
        assert get_meta(s, "schema_version") == SCHEMA_VERSION
        assert len(s.exec(select(CatalogMeta)).all()) == 1
    eng.dispose()
    assert {"product", "product_evidence", "color_reference", "catalog_meta"} <= _tables(db)


def test_pragmas_wal_and_foreign_keys(engine):
    with engine.connect() as conn:
        assert conn.exec_driver_sql("PRAGMA journal_mode").scalar().lower() == "wal"
        assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1


def test_coexists_with_web_tables(tmp_path):
    db = tmp_path / "app.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE product_prices(product_id TEXT PRIMARY KEY, price INTEGER NOT NULL, updated_at TEXT NOT NULL)")
    con.execute("INSERT INTO product_prices VALUES ('1', 1000, 'x')")
    con.commit()
    con.close()
    eng = make_engine(db)
    create_all(eng)
    eng.dispose()
    con = sqlite3.connect(db)
    try:
        assert con.execute("SELECT price FROM product_prices").fetchone() == (1000,)
    finally:
        con.close()


def test_barcode_unique_only_when_non_empty(engine):
    with Session(engine) as s:
        s.add(Product(product_id="1", product_name="A", barcode=None))
        s.add(Product(product_id="2", product_name="B", barcode=""))
        s.add(Product(product_id="3", product_name="C", barcode=""))
        s.add(Product(product_id="4", product_name="D", barcode="4901"))
        s.commit()
    with Session(engine) as s:
        s.add(Product(product_id="5", product_name="E", barcode="4901"))
        with pytest.raises(IntegrityError):
            s.commit()


def test_gallery_folder_unique(engine):
    with Session(engine) as s:
        s.add(Product(product_id="1", product_name="A", gallery_folder="X"))
        s.commit()
    with Session(engine) as s:
        s.add(Product(product_id="2", product_name="B", gallery_folder="X"))
        with pytest.raises(IntegrityError):
            s.commit()


def test_evidence_unique_per_type_and_fk(engine):
    with Session(engine) as s:
        s.add(Product(product_id="7", product_name="ABA"))
        s.add(ProductEvidence(product_id="7", evidence_type="ocr_keywords", value_json='["ABA"]'))
        s.commit()
    with Session(engine) as s:
        s.add(ProductEvidence(product_id="7", evidence_type="ocr_keywords", value_json='["X"]'))
        with pytest.raises(IntegrityError):
            s.commit()
    with Session(engine) as s:
        s.add(ProductEvidence(product_id="999", evidence_type="color_code", value_json='"BE203"'))
        with pytest.raises(IntegrityError):
            s.commit()


def test_color_reference_checks(engine):
    with Session(engine) as s:
        s.add(ColorReference(color_code="BE203", r=199, g=161, b=148, hex="#C7A194", source="seed"))
        s.commit()
    with Session(engine) as s:
        s.add(ColorReference(color_code="BAD", r=256, g=0, b=0, hex="#000000"))
        with pytest.raises(IntegrityError):
            s.commit()
    with Session(engine) as s:
        s.add(ColorReference(color_code="BAD2", r=0, g=0, b=0, hex="#000000", source="lab"))
        with pytest.raises(IntegrityError):
            s.commit()


def test_set_meta_upsert(engine):
    with Session(engine) as s:
        set_meta(s, "next_product_id", "29")
        s.commit()
        set_meta(s, "next_product_id", "30")
        s.commit()
        assert get_meta(s, "next_product_id") == "30"
        assert get_meta(s, "missing") is None


def test_schema_version_mismatch_raises(engine):
    with Session(engine) as s:
        set_meta(s, "schema_version", "999")
        s.commit()
    with pytest.raises(RuntimeError, match="999"):
        create_all(engine)


def test_import_does_not_load_gpu_libraries():
    code = "import sys, src.catalog.db; print(any(m in sys.modules for m in ('torch', 'faiss', 'transformers')))"
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False"
