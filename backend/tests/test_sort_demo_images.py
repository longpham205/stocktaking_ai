"""Test scripts/sort_demo_images.py: nhóm ảnh demo theo kết quả validate."""

from __future__ import annotations

import csv
import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "sort_demo_images.py"
spec = importlib.util.spec_from_file_location("sort_demo_images", SCRIPT)
sort_demo_images = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sort_demo_images)

FIELDS = ["image_key", "source_path", "crop_id", "gt_product_id", "gt_matched", "final_product_id", "final_status", "correct"]


def _row(key, crop, gt, matched, final, status="accepted", correct=None):
    ok = correct if correct is not None else (matched and gt == final)
    return [key, f"x/images/{key}.jpg", crop, gt, str(matched), final, status, str(ok)]


def _run(tmp_path: Path) -> tuple[Path, Path]:
    run, bench = tmp_path / "run", tmp_path / "bench"
    run.mkdir()
    (bench / "images").mkdir(parents=True)
    rows = [
        _row("a", "c1", "1", True, "1"), _row("a", "c2", "2", True, "2"),  # đúng hết, nhanh
        _row("b", "c3", "1", True, "1"),  # đúng hết, chậm
        _row("c", "c4", "1", True, "2"), _row("c", "c5", "3", True, "3"),  # 1 vật sai SKU
        _row("d", "c6", "1", True, "1"), _row("d", "", "2", False, ""),  # 1 vật bị sót
        _row("e", "c7", "1", True, "1"), _row("e", "c8", "", False, "5"),  # 1 khung thừa
    ]
    with (run / "records.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(FIELDS)
        writer.writerows(rows)
    per_image = [
        {"image_key": "a", "predicted_count": 2, "ground_truth_count": 2, "processing_time_ms": 1000},
        {"image_key": "b", "predicted_count": 1, "ground_truth_count": 1, "processing_time_ms": 3000},
        {"image_key": "c", "predicted_count": 2, "ground_truth_count": 2, "processing_time_ms": 2000},
        {"image_key": "d", "predicted_count": 1, "ground_truth_count": 2, "processing_time_ms": 2000},
        {"image_key": "e", "predicted_count": 2, "ground_truth_count": 1, "processing_time_ms": 2000},
    ]
    (run / "report.json").write_text(json.dumps({"per_image": per_image}), encoding="utf-8")
    for key in "abcde":
        (bench / "images" / f"{key}.jpg").write_bytes(b"jpg")
    return run, bench


def test_classify_counts_missed_extra_and_wrong(tmp_path):
    run, _ = _run(tmp_path)
    rows = {r["image_key"]: r for r in sort_demo_images.classify(run)}

    assert (rows["a"]["missed"], rows["a"]["extra"], rows["a"]["wrong_sku"]) == (0, 0, 0)
    assert (rows["c"]["wrong_sku"], rows["c"]["wrong_detail"]) == (1, "1->2")
    assert (rows["d"]["missed"], rows["d"]["extra"]) == (1, 0)
    assert (rows["e"]["missed"], rows["e"]["extra"]) == (0, 1)


def test_main_groups_and_orders_images(tmp_path):
    run, bench = _run(tmp_path)
    out = tmp_path / "out"

    assert sort_demo_images.main(["--run", str(run), str(bench), "--out", str(out)]) == 0

    assert sorted(p.name for p in (out / "1_dung_het").iterdir()) == ["01_a.jpg", "02_b.jpg"]
    assert [p.name for p in (out / "2_nhan_dien_sai").iterdir()] == ["01_c.jpg"]
    assert sorted(p.name for p in (out / "3_crop_sai").iterdir()) == ["01_d.jpg", "02_e.jpg"]
    assert sort_demo_images.main(["--run", str(run), str(bench), "--out", str(out)]) == 1  # không ghi đè
