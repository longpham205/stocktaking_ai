"""Tests cho scripts/compare_golden.py (phần so sánh thuần, không cần model)."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "compare_golden", Path(__file__).resolve().parents[1] / "scripts" / "compare_golden.py"
)
cg = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(cg)


def _item(pid, status):
    return SimpleNamespace(product_id=pid, status=status)


def _summary(acc=None, unc=None):
    return {"accepted": acc or {}, "uncertain": unc or {}}


def _run(argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        code = cg.main(argv)
    return code, buf.getvalue()


def _w(tmp_path, name, data):
    p = tmp_path / name
    p.write_text(json.dumps(data), encoding="utf-8")
    return str(p)


def test_summarize_items_counts_by_status_and_ignores_rejected():
    items = [_item("7", "accepted"), _item("7", "accepted"), _item("8", "uncertain"), _item("9", "rejected")]
    assert cg.summarize_items(items) == {"accepted": {"7": 2}, "uncertain": {"8": 1}}


def test_summarize_items_accepts_dicts_and_sorts_numeric_ids():
    items = [{"product_id": "12", "status": "accepted"}, {"product_id": "3", "status": "accepted"}]
    assert list(cg.summarize_items(items)["accepted"]) == ["3", "12"]


def test_sku_delta_only_differences():
    assert cg.sku_delta({"1": 2, "2": 1}, {"1": 2, "3": 1}) == {"2": 1, "3": -1}


def test_baseline_identical_all_ok():
    cur = {"a.jpg": _summary({"1": 2}), "b.jpg": _summary({"2": 1}, {"3": 1})}
    rep = cg.compare_to_baseline(cur, dict(cur))
    assert rep["ok"] == ["a.jpg", "b.jpg"] and not rep["regressed"]


def test_baseline_regression_detected_with_sku_delta():
    cur = {"a.jpg": _summary({"1": 1})}
    base = {"a.jpg": _summary({"1": 2})}
    rep = cg.compare_to_baseline(cur, base)
    assert rep["regressed"][0]["diff"] == {"accepted": {"1": -1}}


def test_status_shift_is_a_regression_even_if_total_same():
    cur = {"a.jpg": _summary({}, {"1": 1})}
    base = {"a.jpg": _summary({"1": 1}, {})}
    assert cg.compare_to_baseline(cur, base)["regressed"]


def test_allow_list_downgrades_regression():
    cur, base = {"a.jpg": _summary({"1": 1})}, {"a.jpg": _summary({"1": 2})}
    rep = cg.compare_to_baseline(cur, base, {"a.jpg": "đổi ngưỡng có chủ đích"})
    assert not rep["regressed"] and rep["allowed"][0]["reason"] == "đổi ngưỡng có chủ đích"


def test_missing_and_extra_images():
    rep = cg.compare_to_baseline({"a.jpg": _summary()}, {"b.jpg": _summary()})
    assert rep["missing"] == ["b.jpg"] and rep["extra"] == ["a.jpg"]


def test_expected_perfect_match():
    cur = {"a.jpg": _summary({"1": 2, "2": 1})}
    exp = {"a.jpg": {"counts": {"1": 2, "2": 1}}}
    s = cg.compare_to_expected(cur, exp)["summary"]["shown"]
    assert s["recall"] == 1.0 and s["precision"] == 1.0 and s["exact_image_rate"] == 1.0


def test_expected_counts_uncertain_as_shown_but_not_accepted():
    cur = {"a.jpg": _summary({"1": 1}, {"1": 1})}
    exp = {"a.jpg": {"counts": {"1": 2}}}
    rep = cg.compare_to_expected(cur, exp)["summary"]
    assert rep["shown"]["recall"] == 1.0 and rep["accepted"]["recall"] == 0.5


def test_expected_wrong_sku_hurts_precision_and_recall():
    cur = {"a.jpg": _summary({"8": 1})}
    exp = {"a.jpg": {"counts": {"7": 1}}}
    s = cg.compare_to_expected(cur, exp)["summary"]["shown"]
    assert s["recall"] == 0.0 and s["precision"] == 0.0 and s["exact_image_rate"] == 0.0


def test_expected_missing_current_image_listed():
    rep = cg.compare_to_expected({}, {"a.jpg": {"counts": {"1": 1}}})
    assert rep["missing_current"] == ["a.jpg"]


def test_main_baseline_regression_exit_1_and_ok_exit_0(tmp_path):
    base = _w(tmp_path, "base.json", {"images": {"a.jpg": _summary({"1": 2})}})
    same = _w(tmp_path, "same.json", {"images": {"a.jpg": _summary({"1": 2})}})
    diff = _w(tmp_path, "diff.json", {"images": {"a.jpg": _summary({"1": 1})}})
    assert _run(["--current", same, "--baseline", base])[0] == 0
    code, out = _run(["--current", diff, "--baseline", base])
    assert code == 1 and "LỆCH" in out


def test_main_allow_list_makes_it_pass(tmp_path):
    base = _w(tmp_path, "base.json", {"images": {"a.jpg": _summary({"1": 2})}})
    diff = _w(tmp_path, "diff.json", {"images": {"a.jpg": _summary({"1": 1})}})
    allow = _w(tmp_path, "allow.json", {"a.jpg": "đã xem, chấp nhận"})
    assert _run(["--current", diff, "--baseline", base, "--allow", allow])[0] == 0


def test_main_min_recall_gate(tmp_path):
    cur = _w(tmp_path, "c.json", {"images": {"a.jpg": _summary({"1": 1})}})
    exp = _w(tmp_path, "e.json", {"a.jpg": {"counts": {"1": 2}}})
    assert _run(["--current", cur, "--expected", exp])[0] == 0  # chỉ báo cáo
    assert _run(["--current", cur, "--expected", exp, "--min-recall", "0.9"])[0] == 1


def test_main_save_roundtrip(tmp_path):
    cur = _w(tmp_path, "c.json", {"images": {"a.jpg": _summary({"1": 1})}})
    out = tmp_path / "sub" / "saved.json"
    assert _run(["--current", cur, "--save", str(out)])[0] == 0
    assert cg.load_current_file(out) == {"a.jpg": _summary({"1": 1})}


def test_main_usage_errors_exit_2(tmp_path):
    assert _run([])[0] == 2  # thiếu nguồn
    assert _run(["--config", "x.yaml"])[0] == 2  # thiếu --images
    assert _run(["--current", str(tmp_path / "nope.json")])[0] == 2
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert _run(["--current", str(bad)])[0] == 2
