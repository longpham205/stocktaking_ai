"""So sánh hai `report.json` của `--mode validate` (cổng kiểm chứng Gate G).

Chỉ dùng thư viện chuẩn (chạy được trước/không cần cài phụ thuộc AI).

    python scripts/compare_validate.py <report mới> [<baseline>] [--exact] [--tolerance X]

- `<report>` là đường dẫn tới `report.json` hoặc thư mục chứa nó.
- Baseline mặc định: `data/baseline/report.json`.

Chế độ mặc định (Gate G / G-demo): thoát 0 khi các metric gate **không thấp hơn**
baseline (mặc định: `end_to_end.f1` và `fusion.accuracy_after`, tức "Decision
accuracy sau fusion"). Chỉ báo delta của mọi metric số khác, không chặn.

`--exact` (chỉ hợp lệ với backend xác định như bộ demo mock): thoát 0 khi báo cáo
GIỐNG HỆT baseline, bỏ qua các khoá thời gian (`latency`, `*_ms`, `time`, ...) và
đường dẫn tuyệt đối (`source_path`, ...).

Mã thoát: 0 đạt · 1 không đạt · 2 lỗi dùng/đọc file/thiếu metric (không bao giờ
"cho qua" khi không so sánh được).
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

DEFAULT_BASELINE = "data/baseline/report.json"
DEFAULT_GATE_METRICS = ("end_to_end.f1", "fusion.accuracy_after")

# Khoá bị bỏ qua khi so `--exact` (khác nhau giữa các lần chạy/máy dù logic không đổi).
_IGNORE_EXACT_SUBSTR = ("latency", "time", "duration", "elapsed")
_IGNORE_EXACT_SUFFIX = ("_ms", "_path")
# `crop_id` do uuid4 sinh ngẫu nhiên mỗi lần chạy (đã gặp ở lần chạy demo đầu tiên); bỏ qua khi so exact.
_IGNORE_EXACT_NAMES = {"source_path", "image_path", "timestamp", "path", "crop_id"}


class ReportError(Exception):
    """Lỗi đọc/so sánh báo cáo (thoát mã 2)."""


def load_report(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if p.is_dir():
        p = p / "report.json"
    if not p.is_file():
        raise ReportError(f"Không tìm thấy report: {p}")
    try:
        with p.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except json.JSONDecodeError as exc:
        raise ReportError(f"JSON không hợp lệ: {p} ({exc})") from exc
    if not isinstance(data, dict):
        raise ReportError(f"Report phải là object JSON: {p}")
    return data


def get_path(report: dict[str, Any], dotted: str) -> Any:
    """Lấy giá trị theo đường dẫn 'a.b.c'; trả None nếu thiếu."""
    node: Any = report
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and not math.isnan(value)


def gate_metrics(
    new: dict[str, Any], base: dict[str, Any], metrics: tuple[str, ...], tolerance: float
) -> list[dict[str, Any]]:
    """So từng metric gate. Mỗi kết quả: name, base, new, delta, status (ok|lower|within_tol)."""
    rows: list[dict[str, Any]] = []
    for name in metrics:
        b, n = get_path(base, name), get_path(new, name)
        if not _is_number(b):
            raise ReportError(f"Baseline thiếu metric số '{name}' (giá trị: {b!r}); không thể so sánh.")
        if not _is_number(n):
            raise ReportError(f"Report mới thiếu metric số '{name}' (giá trị: {n!r}); không thể so sánh.")
        delta = n - b
        if delta >= 0:
            status = "ok"
        elif -delta <= tolerance:
            status = "within_tol"
        else:
            status = "lower"
        rows.append({"name": name, "base": b, "new": n, "delta": delta, "status": status})
    return rows


_EXTRA_IGNORE: set[str] = set()  # thêm bằng --ignore


def _ignored(key: str) -> bool:
    k = key.lower()
    return (
        k in _IGNORE_EXACT_NAMES
        or k in _EXTRA_IGNORE
        or any(s in k for s in _IGNORE_EXACT_SUBSTR)
        or any(k.endswith(s) for s in _IGNORE_EXACT_SUFFIX)
    )


def exact_diff(new: Any, base: Any, atol: float = 1e-9, _path: str = "") -> list[str]:
    """Danh sách đường dẫn khác nhau giữa hai cấu trúc JSON (bỏ qua khoá thời gian/đường dẫn)."""
    diffs: list[str] = []
    if isinstance(new, dict) and isinstance(base, dict):
        for key in sorted(set(new) | set(base), key=str):
            if _ignored(str(key)):
                continue
            here = f"{_path}.{key}" if _path else str(key)
            if key not in new:
                diffs.append(f"{here}: thiếu ở report mới")
            elif key not in base:
                diffs.append(f"{here}: thừa ở report mới")
            else:
                diffs.extend(exact_diff(new[key], base[key], atol, here))
    elif isinstance(new, list) and isinstance(base, list):
        if len(new) != len(base):
            diffs.append(f"{_path}: độ dài {len(new)} != {len(base)}")
        for i, (a, b) in enumerate(zip(new, base)):
            diffs.extend(exact_diff(a, b, atol, f"{_path}[{i}]"))
    elif _is_number(new) and _is_number(base):
        if abs(new - base) > atol:
            diffs.append(f"{_path}: {base!r} -> {new!r}")
    elif new != base:
        diffs.append(f"{_path}: {base!r} -> {new!r}")
    return diffs


def numeric_leaves(node: Any, prefix: str = "") -> dict[str, float]:
    """Mọi metric số dạng lá (bỏ `records`, `per_image`, khoá thời gian) để in delta tham khảo."""
    out: dict[str, float] = {}
    if isinstance(node, dict):
        for key, value in node.items():
            if prefix == "" and key in ("records", "per_image", "errors"):
                continue
            if _ignored(str(key)):
                continue
            out.update(numeric_leaves(value, f"{prefix}.{key}" if prefix else str(key)))
    elif _is_number(node):
        out[prefix] = float(node)
    return out


def delta_table(new: dict[str, Any], base: dict[str, Any]) -> list[tuple[str, float, float, float]]:
    n, b = numeric_leaves(new), numeric_leaves(base)
    rows = [(k, b[k], n[k], n[k] - b[k]) for k in sorted(set(n) & set(b))]
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="So sánh hai report.json (Gate G).")
    ap.add_argument("new", help="report.json mới (hoặc thư mục chứa nó)")
    ap.add_argument("baseline", nargs="?", default=DEFAULT_BASELINE, help=f"baseline (mặc định {DEFAULT_BASELINE})")
    ap.add_argument("--exact", action="store_true", help="đòi báo cáo giống hệt baseline (bỏ qua khoá thời gian)")
    ap.add_argument("--atol", type=float, default=1e-9, help="sai số tuyệt đối cho --exact (mặc định 1e-9)")
    ap.add_argument("--tolerance", type=float, default=0.0,
                    help="cho phép thấp hơn baseline tối đa X (mặc định 0 = nghiêm ngặt)")
    ap.add_argument("--metric", action="append", default=None,
                    help=f"đường dẫn metric gate (lặp lại được; mặc định {', '.join(DEFAULT_GATE_METRICS)})")
    ap.add_argument("--ignore", action="append", default=[],
                    help="thêm tên khoá bỏ qua khi so --exact (lặp lại được), ví dụ --ignore crop_id")
    ap.add_argument("--all-deltas", action="store_true", help="in cả metric không đổi")
    args = ap.parse_args(argv)
    # Windows: stdout bị chuyển hướng dùng cp1252 -> in tiếng Việt sẽ lỗi.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    _EXTRA_IGNORE.clear()
    _EXTRA_IGNORE.update(k.lower() for k in args.ignore)

    try:
        new = load_report(args.new)
        base = load_report(args.baseline)
        metrics = tuple(args.metric) if args.metric else DEFAULT_GATE_METRICS
        rows = gate_metrics(new, base, metrics, args.tolerance)
    except ReportError as exc:
        print(f"LỖI: {exc}", file=sys.stderr)
        return 2

    print(f"Report mới : {args.new}\nBaseline   : {args.baseline}\n")
    print("== Metric gate ==")
    failed = False
    for r in rows:
        mark = {"ok": "OK  ", "within_tol": "TOL ", "lower": "FAIL"}[r["status"]]
        print(f"  [{mark}] {r['name']:<28} {r['base']:.4f} -> {r['new']:.4f}  ({r['delta']:+.4f})")
        failed |= r["status"] == "lower"

    print("\n== Delta các metric khác (chỉ tham khảo) ==")
    changed = [(k, b, n, d) for k, b, n, d in delta_table(new, base) if abs(d) > 1e-12]
    shown = changed if not args.all_deltas else delta_table(new, base)
    if not shown:
        print("  (không có thay đổi)")
    for k, b, n, d in shown[:60]:
        print(f"  {k:<48} {b:>10.4f} -> {n:>10.4f}  ({d:+.4f})")
    if len(shown) > 60:
        print(f"  ... và {len(shown) - 60} dòng nữa")

    if args.exact:
        diffs = exact_diff(new, base, args.atol)
        print(f"\n== --exact: {len(diffs)} khác biệt (bỏ qua khoá thời gian/đường dẫn) ==")
        for line in diffs[:40]:
            print(f"  {line}")
        if len(diffs) > 40:
            print(f"  ... và {len(diffs) - 40} khác biệt nữa")
        failed |= bool(diffs)

    print("\nKẾT QUẢ:", "KHÔNG ĐẠT" if failed else "ĐẠT")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
