"""Test đồng bộ gallery -> catalog (engine/catalog/reconcile.py, sync_gallery.py)."""

from __future__ import annotations

import shutil

import cv2
import numpy as np
import pytest

from engine.catalog.db import Product, Session, get_meta, make_engine
from engine.catalog.migrate import LegacySources, apply_plan, build_plan
from engine.catalog.reconcile import id_folder_name, reconcile
from engine.catalog.sync_gallery import main, sync_gallery


def _img(folder, n=1):
    folder.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        cv2.imwrite(str(folder / f"{i:02d}.png"), np.full((20, 20, 3), 40 * (i + 1), dtype=np.uint8))


@pytest.fixture()
def setup(tmp_path):
    gallery = tmp_path / "gallery"
    _img(gallery / "A", 2)
    _img(gallery / "B", 1)
    db = tmp_path / "app.db"
    eng = make_engine(db)
    src = LegacySources(
        products=[{"product_id": "1", "product_name": "A", "folder": "A"}, {"product_id": "2", "product_name": "B", "folder": "B"}],
        product_ids={"1": "A", "2": "B"}, product_ids_next=29, colors={},
        gallery_folders={"A": 2, "B": 1},
    )
    apply_plan(eng, build_plan(src))
    yield eng, gallery, tmp_path, db
    eng.dispose()


def test_reconcile_pure():
    plan = reconcile({"A": "1", "B": "2"}, {"1": 2, "2": 5}, {"A": 2, "B": 1, "C": 3})
    assert plan.new_folders == ["C"]
    assert plan.image_count_updates == {"2": 1}
    assert reconcile({"A": "1"}, {"1": 1}, {}).missing_folders == {"1": "A"}
    assert id_folder_name("29") == "0029"


def test_new_gallery_folder_creates_sku_with_next_id(setup):
    eng, gallery, _, _ = setup
    _img(gallery / "New folder", 3)
    res = sync_gallery(eng, gallery)
    assert res.created == {"29": "New folder"}
    with Session(eng) as s:
        p = s.get(Product, "29")
        assert p.needs_naming and p.image_count == 3
        assert get_meta(s, "next_product_id") == "30"
    assert sync_gallery(eng, gallery).created == {}  # idempotent


def test_missing_folder_warns_never_deletes_and_ids_not_reused(setup):
    eng, gallery, _, _ = setup
    _img(gallery / "C")
    sync_gallery(eng, gallery)  # C -> 29
    shutil.rmtree(gallery / "C")
    res = sync_gallery(eng, gallery)
    assert res.plan.missing_folders == {"29": "C"}
    _img(gallery / "D")
    assert sync_gallery(eng, gallery).created == {"30": "D"}
    with Session(eng) as s:
        assert s.get(Product, "29") is not None


def test_inbox_import_assigns_id_and_renames_folder(setup):
    eng, gallery, tmp, _ = setup
    inbox = tmp / "inbox"
    _img(inbox / "anh moi", 2)
    (inbox / "rong").mkdir()
    res = sync_gallery(eng, gallery, inbox)
    assert res.imported == {"anh moi": "29"}
    assert (gallery / "0029").is_dir() and not (inbox / "anh moi").exists()
    assert (inbox / "rong").is_dir()  # thư mục không có ảnh: bỏ qua
    with Session(eng) as s:
        p = s.get(Product, "29")
        assert p.gallery_folder == "0029" and p.needs_naming and "anh moi" in p.product_name


def test_image_count_update_and_dry_run(setup):
    eng, gallery, _, _ = setup
    _img(gallery / "B", 4)
    _img(gallery / "E")
    res = sync_gallery(eng, gallery, dry_run=True)
    assert res.plan.image_count_updates == {"2": 4} and res.created == {}
    with Session(eng) as s:
        assert s.get(Product, "2").image_count == 1 and s.get(Product, "29") is None
    sync_gallery(eng, gallery)
    with Session(eng) as s:
        assert s.get(Product, "2").image_count == 4


def test_cli_requires_existing_db(tmp_path):
    assert main(["--db", str(tmp_path / "x.db"), "--gallery-dir", str(tmp_path)]) == 2
