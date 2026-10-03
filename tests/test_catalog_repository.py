"""Test CatalogRepository: SQLite, snapshot JSON và contract giữa hai nguồn."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from src.catalog.db import ColorReference, Product, ProductEvidence, Session, create_all, make_engine
from src.catalog.repository import CatalogRepository, SqliteCatalogRepository
from src.catalog.snapshot import JsonSnapshotCatalogRepository, write_snapshot

ROOT = Path(__file__).resolve().parents[1]


def _seed(db: Path) -> None:
    eng = make_engine(db)
    create_all(eng)
    with Session(eng) as s:
        s.add(Product(product_id="5", product_name="Shadow (BE203)", barcode="4901", gallery_folder="F5", image_count=6))
        s.add(Product(product_id="7", product_name="ABA", gallery_folder="F7"))
        s.add(Product(product_id="8", product_name="ABC", gallery_folder="F8"))
        s.add(Product(product_id="12", product_name="Old", gallery_folder="F12", is_active=False))
        s.add(Product(product_id="29", product_name="SKU 29", needs_naming=True))
        s.flush()
        for pid, typ, val in [
            ("5", "force_evidence", ["barcode", "color"]),
            ("5", "color_code", "BE203"),
            ("5", "ocr_keywords", ["BE203"]),
            ("7", "force_evidence", ["ocr"]),
            ("7", "ocr_keywords", ["ABA"]),
            ("7", "confusable_with", ["8"]),
            ("8", "force_evidence", ["ocr"]),
            ("8", "ocr_keywords", ["ABC"]),
            ("8", "confusable_with", ["7"]),
        ]:
            s.add(ProductEvidence(product_id=pid, evidence_type=typ, value_json=json.dumps(val)))
        s.add(ColorReference(color_code="BE203", r=199, g=161, b=148, hex="#C7A194", source="seed"))
        s.commit()
    eng.dispose()


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "app.db"
    _seed(path)
    return path


@pytest.fixture(params=["sqlite", "snapshot"])
def repo(request, db, tmp_path):
    sqlite_repo = SqliteCatalogRepository(db)
    if request.param == "sqlite":
        return sqlite_repo
    return JsonSnapshotCatalogRepository(write_snapshot(sqlite_repo.data, tmp_path / "snap.json"))


# ---- hành vi chung (chạy cho cả hai nguồn) ----

def test_satisfies_protocol(repo):
    assert isinstance(repo, CatalogRepository)


def test_products_sorted_numerically(repo):
    assert list(repo.products()) == ["5", "7", "8", "12", "29"]
    assert repo.get_product("5")["barcode"] == "4901"
    assert repo.get_product(5)["product_name"] == "Shadow (BE203)"
    assert repo.get_product("999") is None


def test_folder_mapping_only_active_with_folder(repo):
    assert repo.folder_to_product_id() == {"F5": "5", "F7": "7", "F8": "8"}


def test_evidence_accessors(repo):
    assert repo.force_evidence("5") == frozenset({"barcode", "color"})
    assert repo.force_evidence("29") == frozenset()
    assert repo.ocr_keywords("7") == ("ABA",)
    assert repo.ocr_keywords("29") == ()
    assert repo.color_code("5") == "BE203"
    assert repo.color_code("7") is None
    assert repo.confusable_pairs() == [frozenset({"7", "8"})]
    assert repo.color_references() == {"BE203": (199, 161, 148)}


def test_evidence_values_are_immutable(repo):
    value = repo.evidence("7", "ocr_keywords")
    assert isinstance(value, tuple)
    with pytest.raises(TypeError):
        repo.data.evidence["7"]["ocr_keywords"] = ("X",)


# ---- contract: hai nguồn trả dữ liệu giống hệt ----

def test_sqlite_and_snapshot_identical(db, tmp_path):
    a = SqliteCatalogRepository(db)
    b = JsonSnapshotCatalogRepository(write_snapshot(a.data, tmp_path / "s.json"))
    assert a.version() == b.version()
    assert a.data.to_snapshot_dict() == b.data.to_snapshot_dict()
    for pid in a.products():
        assert a.get_product(pid) == b.get_product(pid)
        assert a.force_evidence(pid) == b.force_evidence(pid)
        assert a.ocr_keywords(pid) == b.ocr_keywords(pid)
        assert a.color_code(pid) == b.color_code(pid)


# ---- version / reload ----

def test_version_stable_and_changes_on_content(db):
    repo = SqliteCatalogRepository(db)
    v1 = repo.version()
    repo.reload()
    assert repo.version() == v1
    eng = make_engine(db)
    with Session(eng) as s:
        p = s.get(Product, "7")
        p.product_name = "ABA moi"
        s.add(p)
        s.commit()
    eng.dispose()
    assert repo.version() == v1  # chưa reload thì vẫn bản cũ
    old_data = repo.data
    repo.reload()
    assert repo.version() != v1
    assert old_data.products["7"].product_name == "ABA"  # bản cũ không bị đổi


def test_missing_sources_raise(tmp_path):
    with pytest.raises(FileNotFoundError):
        SqliteCatalogRepository(tmp_path / "none.db")
    with pytest.raises(FileNotFoundError):
        JsonSnapshotCatalogRepository(tmp_path / "none.json")


def test_corrupt_value_json_raises(db):
    eng = make_engine(db)
    with Session(eng) as s:
        s.add(ProductEvidence(product_id="29", evidence_type="ocr_keywords", value_json="[bad"))
        s.commit()
    eng.dispose()
    with pytest.raises(ValueError, match="29"):
        SqliteCatalogRepository(db)


def test_tampered_snapshot_rejected(db, tmp_path):
    path = write_snapshot(SqliteCatalogRepository(db).data, tmp_path / "s.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    data["products"][0]["product_name"] = "sua tay"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="sửa tay"):
        JsonSnapshotCatalogRepository(path)


def test_snapshot_does_not_import_db_module(db, tmp_path):
    path = write_snapshot(SqliteCatalogRepository(db).data, tmp_path / "s.json")
    code = (
        "import sys; from src.catalog.snapshot import JsonSnapshotCatalogRepository as R; "
        f"R(r'{path}'); print('src.catalog.db' in sys.modules, 'sqlmodel' in sys.modules)"
    )
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False False"


def test_export_script_roundtrip(db, tmp_path):
    out = tmp_path / "x" / "snap.json"
    res = subprocess.run(
        [sys.executable, "scripts/export_catalog_snapshot.py", "--db", str(db), "--out", str(out)],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
    )
    assert res.returncode == 0, res.stderr
    assert JsonSnapshotCatalogRepository(out).version() == SqliteCatalogRepository(db).version()
