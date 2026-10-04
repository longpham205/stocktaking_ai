"""engine.catalog.edits: ghi trên Session của bên gọi, không commit; bên gọi rollback thì không còn gì."""

from pathlib import Path

import pytest
from sqlmodel import Session

from engine.catalog.db import ColorReference, Product, create_all, make_engine
from engine.catalog.edits import (
    BarcodeTaken,
    ProductNotFound,
    all_evidence,
    put_evidence,
    set_barcode,
    set_color,
    set_name,
)
from engine.catalog.repository import SqliteCatalogRepository


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "catalog.db"
    engine = make_engine(path)
    create_all(engine)
    with Session(engine) as s:
        s.add(Product(product_id="1", product_name="Kem ABA", barcode="8930000000017"))
        s.add(Product(product_id="2", product_name="SKU 2", needs_naming=True))
        s.commit()
    engine.dispose()
    return path


def test_ghi_khong_commit_bên_gọi_rollback_thì_mất(db: Path) -> None:
    engine = make_engine(db)
    with Session(engine) as s:
        assert set_barcode(s, "2", "8930000000024") == (None, "8930000000024")
        s.rollback()
    engine.dispose()
    assert SqliteCatalogRepository(db).get_product("2")["barcode"] is None


def test_barcode_trung_sku_khac_bi_chan_rong_la_bo(db: Path) -> None:
    engine = make_engine(db)
    with Session(engine) as s:
        with pytest.raises(BarcodeTaken):
            set_barcode(s, "2", "8930000000017")
        assert set_barcode(s, "1", "8930000000017") == ("8930000000017", "8930000000017")  # không đổi
        assert set_barcode(s, "1", "") == ("8930000000017", None)
        with pytest.raises(ProductNotFound):
            set_barcode(s, "99", "1234")
        s.commit()
    engine.dispose()
    assert SqliteCatalogRepository(db).get_product("1")["barcode"] is None


def test_dat_ten_go_co_needs_naming(db: Path) -> None:
    engine = make_engine(db)
    with Session(engine) as s:
        assert set_name(s, "2", "SKU 2") == ("SKU 2", True)  # cùng tên nhưng gỡ cờ: vẫn là đổi
        assert set_name(s, "1", "Kem ABA") == ("Kem ABA", False)
        s.commit()
    engine.dispose()
    assert SqliteCatalogRepository(db).get_product("2")["needs_naming"] is False


def test_bang_chung_va_mau_tham_chieu(db: Path) -> None:
    engine = make_engine(db)
    with Session(engine) as s:
        put_evidence(s, "1", "ocr_keywords", ["ABA"], "admin")
        put_evidence(s, "1", "color_code", "BE203", "admin")
        assert set_color(s, "BE203", "#6D3F3E") == (None, "#6D3F3E")
        s.commit()
        put_evidence(s, "1", "color_code", None, "admin")  # xoá
        assert set_color(s, "BE203", "#000000") == ("#6D3F3E", "#000000")
        s.commit()
        assert all_evidence(s) == {"1": {"ocr_keywords": ["ABA"]}}
        assert s.get(ColorReference, "BE203").source == "manual"
        assert set_color(s, "BE203", None) == ("#000000", None)
        s.commit()
    engine.dispose()
    repo = SqliteCatalogRepository(db)
    assert repo.ocr_keywords("1") == ("ABA",) and repo.color_references() == {}
