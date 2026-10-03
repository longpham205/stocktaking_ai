"""Test migrate catalog cũ -> DB (engine/catalog/migrate.py)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from engine.catalog.db import Product, ProductEvidence, Session, make_engine, select
from engine.catalog.migrate import (
    LegacySources,
    MigrationError,
    apply_plan,
    build_plan,
    legacy_catalog_tokens,
    legacy_color_code,
    load_sources,
    main,
)
from engine.catalog.repository import SqliteCatalogRepository

ROOT = Path(__file__).resolve().parents[1]
DEMO_SEED = ROOT / "data_demo" / "seed"
SP = "　"  # dấu cách toàn góc trong tên thư mục thật


def _src(**kw) -> LegacySources:
    base = dict(
        products=[
            {"product_id": "1", "product_name": f"Kem chống nắng", "folder": f"アネッサ{SP}UV", "barcode": "111"},
            {"product_id": "2", "product_name": "Kẻ mắt（BR641）", "folder": f"インテグ{SP}（BR641）", "barcode": ""},
            {"product_id": "5", "product_name": "Phấn mắt（BE203）", "folder": "F5", "barcode": "555"},
            {"product_id": "7", "product_name": "Kem ABA", "folder": "外箱ABA"},
            {"product_id": "8", "product_name": "Kem ABC", "folder": "外箱ABC"},
            {"product_id": "21", "product_name": "CASCAN", "folder": "CASCAN"},
        ],
        product_ids={"1": f"アネッサ{SP}UV", "2": f"インテグ{SP}（BR641）", "5": "F5", "7": "外箱ABA", "8": "外箱ABC", "21": "CASCAN"},
        product_ids_next=22,
        colors={"BE203": {"name": "BE203", "rgb": [199, 161, 148], "hex": "#C7A194"}},
        # id_mapping cũ viết dấu cách thường + có ID khoảng trống 10 (thư mục không tồn tại).
        id_mapping={"1": "アネッサ UV", "2": "インテグ （BR641）", "7": "外箱ABA", "8": "外箱ABC", "10": "7000000005"},
        force_rules={"5": ["barcode", "color"], "7": ["ocr"], "8": ["ocr"]},
        confusable_pairs=[["7", "8"]],
        ocr_min_length=3,
        gallery_folders={f"アネッサ{SP}UV": 14, f"インテグ{SP}（BR641）": 23, "F5": 21, "外箱ABA": 19, "外箱ABC": 21, "CASCAN": 5},
        benchmark_category_ids=[1, 2, 5, 7, 8],
    )
    base.update(kw)
    return LegacySources(**base)


# ---- logic suy diễn cũ ----

def test_legacy_inference_matches_old_reranker():
    assert legacy_catalog_tokens("Kem ABA", 3) == ["KEM", "ABA"]
    assert legacy_catalog_tokens("Phấn mắt hồng nhạt（BE203）", 3) == ["BE203"]
    assert legacy_catalog_tokens("Sun_Fresh", 3) == ["SUN", "FRESH"]
    assert legacy_color_code("Kẻ mắt（BR641）") == "BR641"
    assert legacy_color_code("Kem ABA", {"BE203": 1}) is None
    assert legacy_color_code("Son ABA tint", {"ABA": 1}) == "ABA"


# ---- kế hoạch ----

def test_plan_resolves_ids_with_nfkc_and_skips_gaps():
    plan = build_plan(_src())
    folders = {p.product_id: p.gallery_folder for p in plan.products}
    assert folders["1"] == f"アネッサ{SP}UV"  # lưu đúng tên thư mục thật
    assert "10" not in folders
    assert any("ID 10" in i for i in plan.info)
    assert plan.next_product_id == 22
    assert not any("không có trong gallery" in w for w in plan.warnings)


def test_plan_evidence_from_legacy_logic_drops_shared_tokens():
    plan = build_plan(_src())
    ev = plan.evidence
    assert ev["7"] == {"force_evidence": ["ocr"], "confusable_with": ["8"], "ocr_keywords": ["ABA"]}
    assert ev["8"]["ocr_keywords"] == ["ABC"]
    assert ev["2"] == {"ocr_keywords": ["BR641"], "color_code": "BR641"}
    assert ev["5"]["color_code"] == "BE203"
    assert "1" not in ev  # chỉ có token chung KEM
    assert any("KEM" in i for i in plan.info)
    assert any("BR641" in w for w in plan.warnings)  # thiếu màu tham chiếu: cảnh báo, không dừng


def test_needs_naming_when_name_equals_folder():
    plan = build_plan(_src())
    by = {p.product_id: p for p in plan.products}
    assert by["21"].needs_naming is True
    assert by["7"].needs_naming is False


def test_barcode_override_from_web_wins():
    plan = build_plan(_src(barcode_overrides={"7": "777"}))
    assert {p.product_id: p.barcode for p in plan.products}["7"] == "777"


@pytest.mark.parametrize(
    "kw, needle",
    [
        ({"product_ids": {"1": "KHAC", "5": "F5", "21": "CASCAN"}, "id_mapping": {}}, "xung đột"),
        ({"barcode_overrides": {"7": "111"}}, "Barcode 111"),
        ({"benchmark_category_ids": [1, 99]}, "99"),
        ({"force_rules": {"5": ["sam2"]}}, "plugin lạ"),
        ({"id_mapping": {"1": "外箱ABA"}}, "nhiều ID"),
    ],
)
def test_plan_stops_on_real_conflicts(kw, needle):
    with pytest.raises(MigrationError) as exc:
        build_plan(_src(**kw))
    assert any(needle in e for e in exc.value.errors), exc.value.errors


def test_unmapped_gallery_folder_is_warning():
    gal = dict(_src().gallery_folders, **{"New folder": 3})
    plan = build_plan(_src(gallery_folders=gal))
    assert any("New folder" in w for w in plan.warnings)


# ---- ghi DB ----

def test_apply_idempotent_and_never_overwrites(tmp_path):
    db = tmp_path / "app.db"
    eng = make_engine(db)
    plan = build_plan(_src())
    r1 = apply_plan(eng, plan)
    assert r1.inserted["product"] == 6 and r1.inserted["color"] == 1
    with Session(eng) as s:
        p = s.get(Product, "7")
        p.product_name = "Admin đã đổi tên"
        s.add(p)
        s.commit()
    r2 = apply_plan(eng, build_plan(_src()))
    assert sum(r2.inserted.values()) == 0
    with Session(eng) as s:
        assert s.get(Product, "7").product_name == "Admin đã đổi tên"
        assert all(e.updated_by == "migrate" for e in s.exec(select(ProductEvidence)).all())
    eng.dispose()
    repo = SqliteCatalogRepository(db)
    assert repo.confusable_pairs() == [frozenset({"7", "8"})]


def test_next_product_id_never_decreases(tmp_path):
    eng = make_engine(tmp_path / "app.db")
    apply_plan(eng, build_plan(_src(product_ids_next=40)))
    apply_plan(eng, build_plan(_src(product_ids_next=22)))
    from engine.catalog.db import get_meta

    with Session(eng) as s:
        assert get_meta(s, "next_product_id") == "40"
    eng.dispose()


def test_load_sources_reads_web_overrides(tmp_path):
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "products.json").write_text(json.dumps([{"product_id": "1", "product_name": "A", "folder": "A"}]), encoding="utf-8")
    (seed / "product_ids.json").write_text(json.dumps({"next_id": 2, "products": {"1": "A"}}), encoding="utf-8")
    cfg = tmp_path / "c.yaml"
    cfg.write_text("plugins:\n  ocr:\n    min_text_length: 3\n", encoding="utf-8")
    db = tmp_path / "app.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE product_overrides(product_id TEXT PRIMARY KEY, barcode TEXT NOT NULL, updated_at TEXT NOT NULL)")
    con.execute("INSERT INTO product_overrides VALUES ('1', '999', 'x')")
    con.commit()
    con.close()
    src = load_sources(seed, cfg, db_path=db)
    assert src.barcode_overrides == {"1": "999"}
    assert build_plan(src).products[0].barcode == "999"


# ---- bộ demo (bỏ qua nếu máy không có data_demo) ----

@pytest.mark.skipif(not (DEMO_SEED / "expected_evidence.json").is_file(), reason="chưa sinh data_demo")
def test_demo_seed_migrates_and_matches_expected_evidence(tmp_path):
    db = tmp_path / "app.db"
    rc = main([
        "--seed-dir", str(DEMO_SEED),
        "--legacy-config", str(ROOT / "configs" / "legacy" / "config.demo.legacy.yaml"),
        "--db", str(db),
        "--gallery-dir", str(ROOT / "data_demo" / "gallery"),
        "--benchmark-labels", str(ROOT / "data_demo" / "benchmark_full" / "_annotations.coco.json"),
    ])
    assert rc == 0
    repo = SqliteCatalogRepository(db)
    assert len(repo.products()) == 50
    expected = json.loads((DEMO_SEED / "expected_evidence.json").read_text(encoding="utf-8"))
    for pid, exp in expected.items():
        assert list(repo.ocr_keywords(pid)) == exp["ocr_keywords"]
        assert repo.color_code(pid) == exp["color_code"]
        assert repo.force_evidence(pid) == frozenset(exp["force_evidence"])
        assert repo.get_product(pid)["barcode"] == exp["barcode"]
    v1 = repo.version()
    # Config mới (không còn khoá cũ): force/confusable lấy từ expected_evidence -> DB giống hệt.
    db2 = tmp_path / "app2.db"
    assert main(["--seed-dir", str(DEMO_SEED), "--legacy-config", str(ROOT / "configs" / "config.demo.yaml"), "--db", str(db2)]) == 0
    assert SqliteCatalogRepository(db2).version() == v1
    assert main(["--seed-dir", str(DEMO_SEED), "--legacy-config", str(ROOT / "configs" / "config.demo.yaml"), "--db", str(db)]) == 0
    repo.reload()
    assert repo.version() == v1


def test_manual_evidence_overrides_inferred_and_is_validated():
    plan = build_plan(_src(manual_evidence={"1": {"ocr_keywords": ["anessa", "PERFECT"]}}))
    assert plan.evidence["1"]["ocr_keywords"] == ["ANESSA", "PERFECT"]
    with pytest.raises(MigrationError):
        build_plan(_src(manual_evidence={"99": {"ocr_keywords": ["X"]}}))
    with pytest.raises(MigrationError) as exc:  # cặp 7/8 bắt buộc OCR không được trùng token
        build_plan(_src(manual_evidence={"8": {"ocr_keywords": ["ABA"]}}))
    assert any("trùng" in e for e in exc.value.errors)
