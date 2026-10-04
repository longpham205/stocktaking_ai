"""So sánh kết quả pipeline trên bộ ảnh GOLDEN (số lượng theo SKU) — lưới hồi quy.

Hai câu hỏi khác nhau, hai cách so khác nhau:

1. **Hồi quy** (`--baseline`): kết quả hiện tại có GIỐNG kết quả chụp trên code gốc
   không? Đây là phần **chặn** (thoát 1 nếu có ảnh lệch chưa được giải thích).
2. **Chính xác** (`--expected`): kết quả so với nhãn thật (`expected.json`). Chỉ **báo
   cáo** (recall/precision theo SKU); chỉ chặn khi bạn đặt `--min-recall`/`--min-precision`.

Nguồn kết quả hiện tại (chọn một):
    --config CFG --images DIR     chạy pipeline (cần môi trường đầy đủ)
    --current FILE                đọc file đã lưu trước đó (không cần model)

Ví dụ:
    # chụp baseline trên code gốc
    python scripts/compare_golden.py --config configs/config.demo.yaml \\
        --images data_demo/golden/images --expected data_demo/golden/expected.json \\
        --save data/baseline/demo_golden/golden.json
    # sau khi sửa code
    python scripts/compare_golden.py --config configs/config.demo.yaml \\
        --images data_demo/golden/images --expected data_demo/golden/expected.json \\
        --baseline data/baseline/demo_golden/golden.json [--allow allow.json]

`allow.json`: {"golden_03.jpg": "lý do lệch đã được chấp nhận"}.
Mã thoát: 0 đạt · 1 không đạt · 2 lỗi dùng/đọc file.
"""

from __future__ import annotations

import argparse
import inspect
import json
import sys
from pathlib import Path
from typing import Any

IMAGE_EXT = (".jpg", ".jpeg", ".png", ".bmp", ".webp")
STATUSES = ("accepted", "uncertain")


class GoldenError(Exception):
    """Lỗi dùng/đọc file (thoát mã 2)."""


# --------------------------------------------------------------------------- hàm thuần
def summarize_items(items: list[Any]) -> dict[str, dict[str, int]]:
    """Gom item của một ảnh thành {status: {product_id: số lượng}} (chỉ accepted/uncertain)."""
    out: dict[str, dict[str, int]] = {s: {} for s in STATUSES}
    for item in items:
        status = getattr(item, "status", None) if not isinstance(item, dict) else item.get("status")
        pid = getattr(item, "product_id", None) if not isinstance(item, dict) else item.get("product_id")
        if status not in out:
            continue
        key = str(pid)
        out[status][key] = out[status].get(key, 0) + 1
    return {s: dict(sorted(v.items(), key=lambda kv: (len(kv[0]), kv[0]))) for s, v in out.items()}


def sku_delta(cur: dict[str, int], ref: dict[str, int]) -> dict[str, int]:
    """Chênh lệch số lượng theo SKU (cur - ref), bỏ SKU bằng nhau."""
    keys = set(cur) | set(ref)
    return {k: cur.get(k, 0) - ref.get(k, 0) for k in sorted(keys, key=lambda x: (len(x), x)) if cur.get(k, 0) != ref.get(k, 0)}


def compare_to_baseline(
    current: dict[str, dict], baseline: dict[str, dict], allow: dict[str, str] | None = None
) -> dict[str, Any]:
    """So chính xác từng ảnh theo (accepted, uncertain). Trả các nhóm ảnh: ok, allowed, regressed, missing, extra."""
    allow = allow or {}
    ok, allowed, regressed = [], [], []
    for name in sorted(set(current) & set(baseline)):
        diffs = {s: sku_delta(current[name].get(s, {}), baseline[name].get(s, {})) for s in STATUSES}
        diffs = {s: d for s, d in diffs.items() if d}
        if not diffs:
            ok.append(name)
        elif name in allow:
            allowed.append({"image": name, "diff": diffs, "reason": allow[name]})
        else:
            regressed.append({"image": name, "diff": diffs})
    return {
        "ok": ok,
        "allowed": allowed,
        "regressed": regressed,
        "missing": sorted(set(baseline) - set(current)),  # baseline có, hiện tại thiếu
        "extra": sorted(set(current) - set(baseline)),  # hiện tại có, baseline thiếu
    }


def compare_to_expected(current: dict[str, dict], expected: dict[str, dict]) -> dict[str, Any]:
    """So với nhãn thật. `expected[img]['counts']` = {product_id: n}.

    Hai cách đếm: `shown` = accepted + uncertain (cái thu ngân thấy), `accepted` = chỉ accepted.
    """
    per_image, tot = [], {"shown": [0, 0, 0], "accepted": [0, 0, 0]}  # [tp, pred, gt]
    exact = {"shown": 0, "accepted": 0}
    names = sorted(set(current) & set(expected))
    for name in names:
        gt = {str(k): int(v) for k, v in expected[name].get("counts", {}).items()}
        cur = current[name]
        preds = {
            "accepted": dict(cur.get("accepted", {})),
            "shown": {
                k: cur.get("accepted", {}).get(k, 0) + cur.get("uncertain", {}).get(k, 0)
                for k in set(cur.get("accepted", {})) | set(cur.get("uncertain", {}))
            },
        }
        row = {"image": name}
        for mode, pred in preds.items():
            tp = sum(min(pred.get(k, 0), gt.get(k, 0)) for k in set(pred) | set(gt))
            tot[mode][0] += tp
            tot[mode][1] += sum(pred.values())
            tot[mode][2] += sum(gt.values())
            delta = sku_delta(pred, gt)
            row[mode] = delta
            exact[mode] += int(not delta)
        per_image.append(row)

    def ratio(a: int, b: int) -> float:
        return round(a / b, 4) if b else 0.0

    n = max(len(names), 1)
    summary = {
        m: {
            "recall": ratio(v[0], v[2]),
            "precision": ratio(v[0], v[1]),
            "exact_image_rate": round(exact[m] / n, 4),
        }
        for m, v in tot.items()
    }
    return {
        "summary": summary,
        "per_image": per_image,
        "images_compared": len(names),
        "missing_current": sorted(set(expected) - set(current)),
    }


# --------------------------------------------------------------------------- I/O
def _read_json(path: str | Path, what: str) -> Any:
    p = Path(path)
    if not p.is_file():
        raise GoldenError(f"Không tìm thấy {what}: {p}")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise GoldenError(f"JSON không hợp lệ ({what}): {p} ({exc})") from exc


def load_current_file(path: str | Path) -> dict[str, dict]:
    data = _read_json(path, "file kết quả")
    images = data.get("images") if isinstance(data, dict) else None
    if not isinstance(images, dict):
        raise GoldenError(f"File kết quả phải có khoá 'images': {path}")
    return images


def save_current(path: str | Path, images: dict[str, dict], meta: dict[str, Any]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"meta": meta, "images": images}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def run_pipeline(config_path: str, images_dir: str) -> dict[str, dict]:
    """Chạy InferenceRunner trên từng ảnh (import chậm: cần môi trường đầy đủ)."""
    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    try:
        from engine.core.config import load_config
        from engine.inference.infer import InferenceRunner
    except ImportError as exc:  # thiếu thư viện -> báo rõ, không im lặng
        raise GoldenError(f"Không nạp được pipeline (thiếu thư viện?): {exc}") from exc

    folder = Path(images_dir)
    files = sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXT)
    if not files:
        raise GoldenError(f"Không có ảnh trong: {folder}")
    runner = InferenceRunner(load_config(config_path))
    kwargs = {}
    if "persist" in inspect.signature(runner.run_single).parameters:  # có từ P1-4
        kwargs["persist"] = False
    results: dict[str, dict] = {}
    for i, path in enumerate(files, 1):
        result = runner.run_single(str(path), **kwargs)
        results[path.name] = summarize_items(result.items)
        n_items = sum(sum(v.values()) for v in results[path.name].values())
        print(f"  [{i}/{len(files)}] {path.name}: {n_items} item")
    return results


# --------------------------------------------------------------------------- báo cáo
def _fmt_delta(d: dict[str, int]) -> str:
    return ", ".join(f"SKU {k}: {v:+d}" for k, v in d.items()) or "-"


def print_baseline_report(rep: dict[str, Any]) -> None:
    print(f"\n== Hồi quy so với baseline ==\n  giống hệt: {len(rep['ok'])} ảnh")
    for row in rep["allowed"]:
        print(f"  [CHO PHÉP] {row['image']}: {row['diff']}  (lý do: {row['reason']})")
    for row in rep["regressed"]:
        parts = "; ".join(f"{s}: {_fmt_delta(d)}" for s, d in row["diff"].items())
        print(f"  [LỆCH]     {row['image']}: {parts}")
    for name in rep["missing"]:
        print(f"  [THIẾU]    {name}: có trong baseline, không có trong kết quả hiện tại")
    for name in rep["extra"]:
        print(f"  [THỪA]     {name}: có ở hiện tại, không có trong baseline")


def print_expected_report(rep: dict[str, Any]) -> None:
    print(f"\n== So với nhãn thật ({rep['images_compared']} ảnh, chỉ báo cáo) ==")
    for mode, label in (("shown", "accepted+uncertain"), ("accepted", "chỉ accepted")):
        s = rep["summary"][mode]
        print(f"  {label:<20} recall={s['recall']:.4f}  precision={s['precision']:.4f}  ảnh khớp hoàn toàn={s['exact_image_rate']:.2%}")
    worst = [r for r in rep["per_image"] if r["shown"]][:10]
    if worst:
        print("  Ảnh lệch (accepted+uncertain, tối đa 10):")
        for r in worst:
            print(f"    {r['image']}: {_fmt_delta(r['shown'])}")
    for name in rep["missing_current"]:
        print(f"  [THIẾU] {name}: có trong expected, không có kết quả")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="So kết quả trên ảnh golden (số lượng theo SKU).")
    src = ap.add_argument_group("nguồn kết quả hiện tại (chọn một)")
    src.add_argument("--config", help="config để chạy pipeline")
    src.add_argument("--images", help="thư mục ảnh golden (đi cùng --config)")
    src.add_argument("--current", help="đọc kết quả đã lưu (không chạy pipeline)")
    ap.add_argument("--save", help="lưu kết quả hiện tại vào file này (dùng làm baseline)")
    ap.add_argument("--baseline", help="file kết quả baseline (chặn khi lệch)")
    ap.add_argument("--expected", help="expected.json có nhãn thật (báo cáo)")
    ap.add_argument("--allow", help="allow.json: {ảnh: lý do} lệch đã chấp nhận")
    ap.add_argument("--min-recall", type=float, default=None, help="chặn nếu recall(accepted+uncertain) thấp hơn")
    ap.add_argument("--min-precision", type=float, default=None, help="chặn nếu precision(accepted+uncertain) thấp hơn")
    args = ap.parse_args(argv)
    # Windows: stdout bị chuyển hướng dùng cp1252 -> in tiếng Việt sẽ lỗi.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    try:
        if bool(args.current) == bool(args.config):
            raise GoldenError("Chọn đúng một nguồn: --current, hoặc --config cùng --images.")
        if args.config and not args.images:
            raise GoldenError("--config cần đi kèm --images.")
        current = load_current_file(args.current) if args.current else run_pipeline(args.config, args.images)
        if args.save:
            save_current(args.save, current, {"config": args.config, "images_dir": args.images, "n_images": len(current)})
            print(f"Đã lưu kết quả ({len(current)} ảnh) -> {args.save}")
        failed = False
        if args.baseline:
            allow = _read_json(args.allow, "allow-list") if args.allow else {}
            rep = compare_to_baseline(current, load_current_file(args.baseline), allow)
            print_baseline_report(rep)
            failed |= bool(rep["regressed"] or rep["missing"] or rep["extra"])
        if args.expected:
            exp = _read_json(args.expected, "expected.json")
            rep2 = compare_to_expected(current, exp)
            print_expected_report(rep2)
            s = rep2["summary"]["shown"]
            if args.min_recall is not None and s["recall"] < args.min_recall:
                print(f"  [FAIL] recall {s['recall']:.4f} < {args.min_recall}")
                failed = True
            if args.min_precision is not None and s["precision"] < args.min_precision:
                print(f"  [FAIL] precision {s['precision']:.4f} < {args.min_precision}")
                failed = True
        if not (args.baseline or args.expected or args.save):
            print("Lưu ý: không có --baseline/--expected/--save nên chỉ chạy, không so sánh gì.")
    except GoldenError as exc:
        print(f"LỖI: {exc}", file=sys.stderr)
        return 2

    print("\nKẾT QUẢ:", "KHÔNG ĐẠT" if failed else "ĐẠT")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
