"""Test scripts/split_sku.py (catalog SQLite tạm + thư mục gallery tạm; không mở cửa sổ, không cần Postgres)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from engine.catalog.db import META_NEXT_PRODUCT_ID, Product, Session, create_all, get_meta, make_engine, set_meta

ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location("split_sku", ROOT / "scripts" / "split_sku.py")
ss = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = ss
_SPEC.loader.exec_module(ss)


def _catalog(path: Path, next_product_id: str = "40"):
    engine = make_engine(path)
    create_all(engine)
    with Session(engine) as session:
        session.add(Product(product_id="36", product_name="Sữa rửa mặt", gallery_folder="0036", image_count=4))
        set_meta(session, META_NEXT_PRODUCT_ID, next_product_id)
        session.commit()
    return engine


def _gallery(tmp_path: Path) -> Path:
    folder = tmp_path / "gallery" / "0036"
    folder.mkdir(parents=True)
    for name in ("0001.jpg", "0002.jpg", "0003.jpg", "0004.jpg"):
        (folder / name).write_bytes(b"x")
    return tmp_path / "gallery"


def _state(engine) -> dict:
    with Session(engine) as session:
        new = session.get(Product, "40")
        return {
            "old_count": session.get(Product, "36").image_count,
            "new": None if new is None else (new.product_name, new.gallery_folder, new.image_count, new.needs_naming),
            "next": get_meta(session, META_NEXT_PRODUCT_ID),
        }


def test_split_moves_the_photos_and_writes_every_catalog(tmp_path):
    gallery = _gallery(tmp_path)
    engines = {"a": _catalog(tmp_path / "a.db"), "b": _catalog(tmp_path / "b.db")}
    assert ss.split(engines, gallery, "36", ["0003.jpg", "0004.jpg"], "Sữa rửa mặt B") == "40"
    assert ss.gallery_images(gallery / "0036") == ["0001.jpg", "0002.jpg"]
    assert ss.gallery_images(gallery / "0040") == ["0003.jpg", "0004.jpg"]
    for engine in engines.values():
        assert _state(engine) == {"old_count": 2, "new": ("Sữa rửa mặt B", "0040", 2, False), "next": "41"}
        engine.dispose()


def test_a_split_without_a_name_is_left_to_be_named(tmp_path):
    engine = _catalog(tmp_path / "a.db")
    ss.split({"a": engine}, _gallery(tmp_path), "36", ["0001.jpg"], None)
    assert _state(engine)["new"] == ("Chưa đặt tên (tách từ 36)", "0040", 1, True)
    engine.dispose()


def test_catalogs_that_would_give_different_ids_stop_the_split(tmp_path):
    gallery = _gallery(tmp_path)
    engines = {"a": _catalog(tmp_path / "a.db"), "b": _catalog(tmp_path / "b.db", next_product_id="41")}
    with pytest.raises(ValueError, match="không khớp"):
        ss.split(engines, gallery, "36", ["0001.jpg"], None)
    assert len(ss.gallery_images(gallery / "0036")) == 4
    assert _state(engines["a"]) == {"old_count": 4, "new": None, "next": "40"}
    for engine in engines.values():
        engine.dispose()


def test_dry_run_changes_nothing(tmp_path):
    gallery, engine = _gallery(tmp_path), _catalog(tmp_path / "a.db")
    assert ss.split({"a": engine}, gallery, "36", ["0001.jpg"], None, dry_run=True) == "40"
    assert not (gallery / "0040").exists()
    assert _state(engine) == {"old_count": 4, "new": None, "next": "40"}
    engine.dispose()


@pytest.mark.parametrize(
    ("chosen", "message"),
    [([], "Chưa chọn"), (["0001.jpg", "0002.jpg"], "hết ảnh"), (["nope.jpg"], "Không có trong thư mục")],
)
def test_a_selection_that_cannot_split_is_refused(chosen, message):
    with pytest.raises(ValueError, match=message):
        ss.check_selection(["0001.jpg", "0002.jpg"], chosen)
