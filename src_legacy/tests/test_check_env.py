"""Test scripts/check_env.py (dựng cây thư mục giả, thay thế kiểm tra thư viện/cổng)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import yaml

from backend.db import Database, utcnow

_SPEC = importlib.util.spec_from_file_location("check_env", Path(__file__).resolve().parents[1] / "scripts" / "check_env.py")
ce = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ce)

PRODUCTS = [{"product_id": "1", "product_name": "A", "barcode": "8931000001372"},
            {"product_id": "2", "product_name": "B", "barcode": ""},
            {"product_id": "3", "product_name": "C", "barcode": ""}]


def _build(tmp: Path, *, det="mock_contour", ret="mock_visual_embedding", ref="none", build_index=False,
           with_index=True, with_products=True, with_gallery=True, ghost=False):
    cfg = {"paths": {"metadata_dir": "metadata", "gallery_dir": "gallery"}, "catalog": {"source": "sqlite", "db_path": "webdata/db/app.db"},
           "detection": {"backend": det, "rf_detr": {"weights_path": "weights/det.pt"}},
           "retrieval": {"backend": ret, "gallery_index_path": "cache/idx.faiss", "gallery_metadata_path": "cache/meta.json",
                         "build_gallery_index": build_index, "siglip2": {"weights_path": "weights/siglip"}},
           "refinement": {"enabled": ref != "none", "backend": ref, "sam2": {"checkpoint_path": "weights/sam2.pt"}},
           "plugins": {"ocr": {"enabled": False}, "barcode": {"enabled": False}}}
    (tmp / "cfg.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    if with_products:
        _seed_catalog(tmp / "webdata" / "db" / "app.db")
    if with_gallery:
        (tmp / "gallery" / "p1").mkdir(parents=True)
        (tmp / "gallery" / "p1" / "a.jpg").write_bytes(b"x")
    if with_index:
        (tmp / "cache").mkdir()
        (tmp / "cache" / "idx.faiss").write_bytes(b"x" * 2048)
        ids = ["1", "2", "99"] if ghost else ["1", "2"]
        (tmp / "cache" / "meta.json").write_text(json.dumps({"items": [{"product_id": i} for i in ids]}), encoding="utf-8")


def _seed_catalog(db_path: Path) -> None:
    from src.catalog.db import Product, Session, create_all, make_engine

    eng = make_engine(db_path)
    try:
        create_all(eng)
        with Session(eng) as s:
            for p in PRODUCTS:
                s.add(Product(product_id=p["product_id"], product_name=p["product_name"], barcode=p["barcode"] or None))
            s.commit()
    finally:
        eng.dispose()


def _run(tmp: Path, *, modules=lambda n: True, busy=lambda p: False, port=8000):
    return ce.run_checks(Path("cfg.yaml"), Path("webdata"), port, root=tmp, has_module=modules, port_busy=busy)


def _get(results, name_part):
    hits = [r for r in results if name_part in r[1]]
    assert hits, f"không có mục '{name_part}' trong {[r[1] for r in results]}"
    return hits[0]


def test_everything_ok_has_no_failures(tmp_path):
    _build(tmp_path)
    res = _run(tmp_path)
    assert not [r for r in res if r[0] == ce.FAIL], res
    assert "3 SKU" in _get(res, "Catalog")[2] and "1 thư mục" in _get(res, "Gallery")[2]
    assert "MOCK" in _get(res, "Backend")[2]  # nói rõ đang ở chế độ mock


def test_missing_module_port_busy_products_gallery_index_are_failures(tmp_path):
    _build(tmp_path, with_products=False, with_gallery=False, with_index=False)
    res = _run(tmp_path, modules=lambda n: n != "faiss", busy=lambda p: True)
    for part in ("Thư viện Python", "Catalog", "Gallery", "Index FAISS", "Cổng"):
        assert _get(res, part)[0] == ce.FAIL, part
    assert "faiss-cpu" in _get(res, "Thư viện Python")[2]  # gợi ý đúng lệnh cài


def test_missing_index_is_warning_when_build_enabled(tmp_path):
    _build(tmp_path, with_index=False, build_index=True)
    assert _get(_run(tmp_path), "Index FAISS")[0] == ce.WARN


def test_index_with_unknown_sku_warns(tmp_path):
    _build(tmp_path, ghost=True)
    lvl, _, detail = _get(_run(tmp_path), "Index ↔ catalog")
    assert lvl == ce.WARN and "99" in detail


def test_real_backend_needs_weights_and_packages(tmp_path):
    _build(tmp_path, det="rf_detr", ret="siglip2", ref="sam2")
    res = _run(tmp_path, modules=lambda n: n not in ("torch", "rfdetr", "transformers", "sam2"))
    assert _get(res, "Thư viện Python")[0] == ce.FAIL
    for part in ("Weights detector", "Checkpoint SAM2"):
        assert _get(res, part)[0] == ce.FAIL, part
    assert _get(res, "Weights embedding")[0] == ce.WARN  # có thể tự tải từ internet
    (tmp_path / "weights").mkdir()
    (tmp_path / "weights" / "det.pt").write_bytes(b"x")
    (tmp_path / "weights" / "sam2.pt").write_bytes(b"x")
    res2 = _run(tmp_path)
    assert _get(res2, "Weights detector")[0] == ce.OK and _get(res2, "Checkpoint SAM2")[0] == ce.OK
    assert "CHẾ ĐỘ MOCK" not in _get(res2, "Backend")[2]


def test_database_price_and_barcode_report(tmp_path):
    _build(tmp_path)
    db = Database(tmp_path / "webdata" / "db" / "app.db")
    with db.tx() as c:
        c.execute("INSERT INTO users(username,password_hash,created_at) VALUES('a','h',?)", (utcnow(),))
        c.execute("INSERT INTO product_prices VALUES('1',1000,?)", (utcnow(),))
        c.execute("UPDATE product SET barcode='89310002222' WHERE product_id='2'")
    res = _run(tmp_path)
    lvl, _, detail = _get(res, "Giá sản phẩm")
    assert lvl == ce.WARN and "2/3" in detail
    assert "2/3 SKU có barcode" in _get(res, "Barcode")[2]  # barcode đọc từ bảng catalog `product`
    with db.tx() as c:
        c.execute("INSERT INTO product_prices VALUES('2',1,?)", (utcnow(),))
        c.execute("INSERT INTO product_prices VALUES('3',1,?)", (utcnow(),))
    assert _get(_run(tmp_path), "Giá sản phẩm")[0] == ce.OK


def test_corrupt_database_is_failure(tmp_path):
    _build(tmp_path, with_products=False)
    (tmp_path / "webdata" / "db").mkdir(parents=True)
    (tmp_path / "webdata" / "db" / "app.db").write_bytes(b"day khong phai sqlite" * 200)
    assert _get(_run(tmp_path), "Database web")[0] == ce.FAIL


def test_unwritable_data_dir_is_failure(tmp_path):
    _build(tmp_path, with_products=False)
    (tmp_path / "webdata").write_text("la mot file, khong phai thu muc", encoding="utf-8")
    assert _get(_run(tmp_path), "Thư mục dữ liệu web")[0] == ce.FAIL


def test_missing_or_broken_config_is_failure(tmp_path):
    res = ce.run_checks(Path("nope.yaml"), Path("webdata"), 8000, root=tmp_path, has_module=lambda n: True, port_busy=lambda p: False)
    assert _get(res, "File config")[0] == ce.FAIL
    (tmp_path / "bad.yaml").write_text("a: [unclosed", encoding="utf-8")
    res = ce.run_checks(Path("bad.yaml"), Path("webdata"), 8000, root=tmp_path, has_module=lambda n: True, port_busy=lambda p: False)
    assert _get(res, "File config")[0] == ce.FAIL and "YAML" in _get(res, "File config")[2]


def test_main_exit_codes(tmp_path):
    assert ce.main(["--config", str(tmp_path / "nope.yaml"), "--data-dir", str(tmp_path / "d")]) == 1


def test_fake_mode_skips_pipeline_checks_but_keeps_web_checks(tmp_path):
    _build(tmp_path, with_gallery=False, with_index=False)  # thiếu gallery + index: lỗi khi chạy pipeline thật
    strict = _run(tmp_path)
    assert _get(strict, "Gallery")[0] == ce.FAIL and _get(strict, "Index FAISS")[0] == ce.FAIL
    res = ce.run_checks(Path("cfg.yaml"), Path("webdata"), 8000, root=tmp_path, has_module=lambda n: n != "faiss",
                        port_busy=lambda p: False, fake=True)
    assert not [r for r in res if r[0] == ce.FAIL], res  # --fake: không đòi faiss/gallery/index
    assert "--fake" in _get(res, "Pipeline AI")[2]
    assert _get(res, "Catalog")[0] == ce.OK  # catalog (DB) vẫn bắt buộc với --fake vì web đọc nó
    res2 = ce.run_checks(Path("cfg.yaml"), Path("webdata"), 8000, root=tmp_path, has_module=lambda n: True,
                         port_busy=lambda p: True, fake=True)
    assert _get(res2, "Cổng")[0] == ce.FAIL  # cổng bận vẫn là lỗi


def test_main_ignores_backend_passthrough_args(tmp_path):
    # launch.bat chuyển nguyên tham số của backend: không được làm argparse báo lỗi thoát mã 2
    assert ce.main(["--config", str(tmp_path / "nope.yaml"), "--data-dir", str(tmp_path / "d"), "--host", "0.0.0.0", "-v", "--fake"]) == 1
