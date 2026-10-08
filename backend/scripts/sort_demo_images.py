"""Chia ảnh benchmark thành nhóm để chọn ảnh demo, theo kết quả một (hoặc nhiều) lượt validate.

    python scripts/sort_demo_images.py --run data/baseline/main_1008_new data/benchmark_new \
        --run data/baseline/main_1008_val data/benchmark --out data/demo_sets

Mỗi ``--run <thư mục kết quả validate> <thư mục benchmark của lượt đó>``. Với từng ảnh (khớp khung
không xét SKU, như validate): bỏ sót = nhãn không có khung; thừa = số vật trả về nhiều hơn số nhãn
có khung; sai SKU = khung khớp nhãn nhưng SKU cuối khác nhãn. Nhóm:

    1_dung_het        không sót, không thừa, không sai SKU     -> tăng dần theo thời gian chạy
    2_nhan_dien_sai   khung đúng hết, có vật sai SKU           -> tăng dần theo số vật sai
    3_crop_sai        có vật bị sót hoặc khung thừa            -> tăng dần theo tổng số lỗi

Chép ảnh gốc vào ``<out>/<nhóm>/<thứ tự>_<tên ảnh>`` và ghi ``<out>/danh_sach.csv``. Thư mục
``<out>`` được tạo mới: đã tồn tại thì dừng (không ghi đè).
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
from pathlib import Path

GROUPS = ("1_dung_het", "2_nhan_dien_sai", "3_crop_sai")


def classify(run_dir: Path) -> list[dict]:
    """Một dòng mỗi ảnh: số lỗi theo loại, thời gian chạy, số vật cần xác nhận."""
    report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    per_image = {row["image_key"]: row for row in report["per_image"]}
    records = list(csv.DictReader((run_dir / "records.csv").open(encoding="utf-8")))
    rows = []
    for key, info in per_image.items():
        mine = [r for r in records if r["image_key"] == key]
        matched = [r for r in mine if r["crop_id"] and r["gt_matched"] == "True"]
        missed = sum(1 for r in mine if not r["crop_id"])
        extra = max(0, int(info["predicted_count"]) - len(matched))
        wrong = [r for r in matched if r["correct"] != "True"]
        rows.append({
            "image_key": key,
            "source_path": mine[0]["source_path"] if mine else "",
            "gt": int(info["ground_truth_count"]),
            "missed": missed,
            "extra": extra,
            "wrong_sku": len(wrong),
            "wrong_detail": " ".join(f"{r['gt_product_id']}->{r['final_product_id']}" for r in wrong),
            "uncertain": sum(1 for r in mine if r["crop_id"] and r["final_status"] == "uncertain"),
            "seconds": round(float(info["processing_time_ms"]) / 1000, 1),
        })
    return rows


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Chia ảnh benchmark thành nhóm để chọn ảnh demo.")
    parser.add_argument("--run", nargs=2, action="append", required=True, metavar=("KET_QUA", "BENCHMARK"))
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    if args.out.exists():
        print(f"Dừng: {args.out} đã tồn tại (không ghi đè).", file=sys.stderr)
        return 1
    rows = []
    for run_dir, bench_dir in args.run:
        for row in classify(Path(run_dir)):
            row["benchmark"] = bench_dir
            row["file"] = Path(bench_dir) / "images" / Path(row["source_path"]).name
            if row["missed"] or row["extra"]:
                row["group"], row["sort"] = GROUPS[2], (row["missed"] + row["extra"] + row["wrong_sku"], row["seconds"])
            elif row["wrong_sku"]:
                row["group"], row["sort"] = GROUPS[1], (row["wrong_sku"], row["seconds"])
            else:
                row["group"], row["sort"] = GROUPS[0], (row["seconds"],)
            rows.append(row)

    out_rows = []
    for group in GROUPS:
        target = args.out / group
        target.mkdir(parents=True)
        for order, row in enumerate(sorted((r for r in rows if r["group"] == group), key=lambda r: r["sort"]), start=1):
            name = f"{order:02d}_{Path(row['file']).name}"
            shutil.copy2(row["file"], target / name)
            out_rows.append({"nhom": group, "thu_tu": order, "file": f"{group}/{name}", "benchmark": row["benchmark"],
                             "so_nhan": row["gt"], "bo_sot": row["missed"], "thua": row["extra"], "sai_sku": row["wrong_sku"],
                             "chi_tiet_sai": row["wrong_detail"], "can_xac_nhan": row["uncertain"], "giay": row["seconds"]})
        print(f"{group}: {sum(1 for r in rows if r['group'] == group)} ảnh")
    with (args.out / "danh_sach.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(out_rows[0]))
        writer.writeheader()
        writer.writerows(out_rows)
    print(f"-> {args.out} ({len(out_rows)} ảnh), danh sách: {args.out / 'danh_sach.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
