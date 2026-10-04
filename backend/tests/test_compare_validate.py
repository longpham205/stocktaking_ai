"""Tests cho scripts/compare_validate.py (chỉ dùng thư viện chuẩn, không cần model)."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "compare_validate", Path(__file__).resolve().parents[1] / "scripts" / "compare_validate.py"
)
cv = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(cv)


def _report(f1=0.939, acc=0.936, extra=None):
    rep = {
        "dataset": {"total_images": 31},
        "end_to_end": {"f1": f1, "precision": 0.913, "latency_ms": 12.0},
        "fusion": {"accuracy_before": 0.712, "accuracy_after": acc, "mean_fusion_latency_ms": 3.2},
        "latency": {"end_to_end_ms": {"mean": 100.0}},
        "records": [{"image_key": "a", "source_path": "C:/x/a.jpg", "detection_confidence": 0.9, "decision_latency_ms": 1.0}],
    }
    if extra:
        rep.update(extra)
    return rep


def _write(tmp_path, name, data):
    p = tmp_path / name
    p.write_text(json.dumps(data), encoding="utf-8")
    return str(p)


def _run(argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        code = cv.main(argv)
    return code, buf.getvalue()


def test_gate_equal_passes(tmp_path):
    a, b = _write(tmp_path, "a.json", _report()), _write(tmp_path, "b.json", _report())
    code, _ = _run([a, b])
    assert code == 0


def test_gate_higher_passes(tmp_path):
    a = _write(tmp_path, "a.json", _report(f1=0.95, acc=0.94))
    b = _write(tmp_path, "b.json", _report())
    assert _run([a, b])[0] == 0


def test_gate_lower_f1_fails(tmp_path):
    a = _write(tmp_path, "a.json", _report(f1=0.938))
    b = _write(tmp_path, "b.json", _report())
    code, out = _run([a, b])
    assert code == 1 and "FAIL" in out and "end_to_end.f1" in out


def test_gate_lower_decision_accuracy_fails(tmp_path):
    a = _write(tmp_path, "a.json", _report(acc=0.9))
    b = _write(tmp_path, "b.json", _report())
    code, out = _run([a, b])
    assert code == 1 and "fusion.accuracy_after" in out


def test_tolerance_allows_tiny_drop(tmp_path):
    a = _write(tmp_path, "a.json", _report(f1=0.9385))
    b = _write(tmp_path, "b.json", _report())
    assert _run([a, b])[0] == 1
    assert _run([a, b, "--tolerance", "0.001"])[0] == 0


def test_missing_metric_is_error_not_pass(tmp_path):
    rep = _report()
    del rep["fusion"]["accuracy_after"]
    a, b = _write(tmp_path, "a.json", rep), _write(tmp_path, "b.json", _report())
    code, out = _run([a, b])
    assert code == 2 and "accuracy_after" in out


def test_na_metric_is_error(tmp_path):
    rep = _report()
    rep["end_to_end"]["f1"] = "n/a"
    a, b = _write(tmp_path, "a.json", rep), _write(tmp_path, "b.json", _report())
    assert _run([a, b])[0] == 2


def test_missing_file_is_error(tmp_path):
    b = _write(tmp_path, "b.json", _report())
    assert _run([str(tmp_path / "nope.json"), b])[0] == 2


def test_directory_resolves_report_json(tmp_path):
    d = tmp_path / "out"
    d.mkdir()
    (d / "report.json").write_text(json.dumps(_report()), encoding="utf-8")
    b = _write(tmp_path, "b.json", _report())
    assert _run([str(d), b])[0] == 0


def test_exact_ignores_time_and_paths(tmp_path):
    new = _report()
    new["latency"]["end_to_end_ms"]["mean"] = 999.0
    new["end_to_end"]["latency_ms"] = 77.0
    new["fusion"]["mean_fusion_latency_ms"] = 9.9
    new["records"][0]["source_path"] = "D:/other/a.jpg"
    new["records"][0]["decision_latency_ms"] = 55.0
    a, b = _write(tmp_path, "a.json", new), _write(tmp_path, "b.json", _report())
    assert _run([a, b, "--exact"])[0] == 0


def test_exact_detects_real_change(tmp_path):
    new = _report()
    new["records"][0]["detection_confidence"] = 0.8
    a, b = _write(tmp_path, "a.json", new), _write(tmp_path, "b.json", _report())
    code, out = _run([a, b, "--exact"])
    assert code == 1 and "detection_confidence" in out


def test_exact_detects_extra_and_missing_keys(tmp_path):
    new = _report(extra={"surprise": 1})
    a, b = _write(tmp_path, "a.json", new), _write(tmp_path, "b.json", _report())
    code, out = _run([a, b, "--exact"])
    assert code == 1 and "surprise" in out


def test_exact_list_length_difference(tmp_path):
    new = _report()
    new["records"].append({"image_key": "b"})
    a, b = _write(tmp_path, "a.json", new), _write(tmp_path, "b.json", _report())
    assert _run([a, b, "--exact"])[0] == 1


def test_exact_same_but_gate_lower_still_fails(tmp_path):
    # --exact không được che gate: F1 thấp hơn vẫn phải fail dù có --exact
    new = _report(f1=0.9)
    a, b = _write(tmp_path, "a.json", new), _write(tmp_path, "b.json", _report())
    assert _run([a, b, "--exact"])[0] == 1


def test_custom_metric_path(tmp_path):
    a, b = _write(tmp_path, "a.json", _report()), _write(tmp_path, "b.json", _report())
    assert _run([a, b, "--metric", "end_to_end.precision"])[0] == 0


def test_ignored_key_rules():
    assert cv._ignored("source_path") and cv._ignored("decision_latency_ms") and cv._ignored("timestamp")
    assert not cv._ignored("f1") and not cv._ignored("accuracy_after") and not cv._ignored("detection_confidence")


def test_exact_ignores_random_crop_id(tmp_path):
    new = _report()
    new["records"][0]["crop_id"] = "crop_aaaaaaaaaaaa"
    base = _report()
    base["records"][0]["crop_id"] = "crop_bbbbbbbbbbbb"
    a, b = _write(tmp_path, "a.json", new), _write(tmp_path, "b.json", base)
    assert _run([a, b, "--exact"])[0] == 0


def test_exact_still_detects_change_next_to_crop_id(tmp_path):
    new = _report()
    new["records"][0]["crop_id"] = "crop_aaaaaaaaaaaa"
    new["records"][0]["detection_confidence"] = 0.5
    base = _report()
    base["records"][0]["crop_id"] = "crop_bbbbbbbbbbbb"
    a, b = _write(tmp_path, "a.json", new), _write(tmp_path, "b.json", base)
    code, out = _run([a, b, "--exact"])
    assert code == 1 and "detection_confidence" in out and "crop_id" not in out


def test_extra_ignore_option(tmp_path):
    new = _report()
    new["records"][0]["detection_confidence"] = 0.5
    a, b = _write(tmp_path, "a.json", new), _write(tmp_path, "b.json", _report())
    assert _run([a, b, "--exact"])[0] == 1
    assert _run([a, b, "--exact", "--ignore", "detection_confidence"])[0] == 0
