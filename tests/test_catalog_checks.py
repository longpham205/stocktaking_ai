"""Test kiểm nhất quán catalog lúc khởi động (src/catalog/checks.py)."""

from __future__ import annotations

import json

import faiss
import numpy as np
import pytest

from src.catalog.checks import check_catalog
from src.catalog.db import ColorReference, Product, ProductEvidence, Session, create_all, make_engine
from src.catalog.repository import SqliteCatalogRepository


@pytest.fixture()
def repo(tmp_path):
    db = tmp_path / "app.db"
    eng = make_engine(db)
    create_all(eng)
    with Session(eng) as s:
        s.add(Product(product_id="1", product_name="A", gallery_folder="A"))
        s.add(Product(product_id="2", product_name="B", gallery_folder="B"))
        s.flush()
        s.add(ProductEvidence(product_id="2", evidence_type="color_code", value_json='"BR641"'))
        s.add(ColorReference(color_code="BE203", r=1, g=2, b=3, hex="#010203", source="seed"))
        s.commit()
    eng.dispose()
    return SqliteCatalogRepository(db)


def _index(tmp_path, ids, dim=8):
    meta = tmp_path / "meta.json"
    meta.write_text(json.dumps([{"product_id": i} for i in ids]), encoding="utf-8")
    idx = faiss.IndexFlatIP(dim)
    idx.add(np.random.default_rng(0).random((len(ids), dim), dtype=np.float32))
    path = tmp_path / "g.faiss"
    faiss.write_index(idx, str(path))
    return meta, path


def test_consistent_catalog_has_only_color_warning(repo, tmp_path):
    meta, idx = _index(tmp_path, ["1", "1", "2"])
    rep = check_catalog(repo, ocr_min_length=3, gallery_metadata_path=meta, gallery_index_path=idx, embedding_dim=8)
    assert rep.ok, rep.errors
    assert any("BR641" in w for w in rep.warnings)


def test_unknown_index_product_and_dim_mismatch(repo, tmp_path):
    meta, idx = _index(tmp_path, ["1", "9"])
    rep = check_catalog(repo, ocr_min_length=3, gallery_metadata_path=meta, gallery_index_path=idx, embedding_dim=768)
    assert any("['9']" in e for e in rep.errors)
    assert any("768" in e for e in rep.errors)
    assert any("['2']" in w for w in rep.warnings)  # SKU 2 chưa có vector


def test_count_mismatch_and_missing_files(repo, tmp_path):
    meta, idx = _index(tmp_path, ["1", "2"])
    meta.write_text(json.dumps([{"product_id": "1"}]), encoding="utf-8")
    rep = check_catalog(repo, ocr_min_length=3, gallery_metadata_path=meta, gallery_index_path=idx)
    assert any("2 vector" in e for e in rep.errors)
    rep = check_catalog(repo, ocr_min_length=3, gallery_metadata_path=tmp_path / "x.json")
    assert any("Không thấy" in e for e in rep.errors)


def test_benchmark_categories(repo, tmp_path):
    labels = tmp_path / "coco.json"
    labels.write_text(json.dumps({"annotations": [{"category_id": 1}, {"category_id": 3}]}), encoding="utf-8")
    rep = check_catalog(repo, ocr_min_length=3, benchmark_labels=labels)
    assert any("[3]" in e for e in rep.errors)
