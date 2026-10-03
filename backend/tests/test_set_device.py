"""Test scripts/set_device.py (trên bản sao config, không đụng configs/config.yaml)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location("set_device", ROOT / "scripts" / "set_device.py")
sd = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(sd)


@pytest.mark.parametrize("name", ["config.yaml", "config.demo.yaml"])
def test_real_configs_have_expected_device_keys(name):
    """Config thêm/mất khoá device thì phải cập nhật EXPECTED_KEYS (và set_device) cùng lúc."""
    assert len(sd.read_devices(ROOT / "configs" / name)) == sd.EXPECTED_KEYS


def test_apply_changes_only_device_values_and_round_trips(tmp_path):
    src = (ROOT / "configs" / "config.yaml").read_bytes()
    cfg = tmp_path / "config.yaml"
    cfg.write_bytes(src)
    start = {v for _, v in sd.read_devices(cfg)}
    assert len(start) == 1
    other = "cpu" if start == {"cuda"} else "cuda"

    assert sd.apply_device(other, cfg) == sd.EXPECTED_KEYS
    assert {v for _, v in sd.read_devices(cfg)} == {other}
    changed = [(a, b) for a, b in zip(src.splitlines(True), cfg.read_bytes().splitlines(True)) if a != b]
    assert len(changed) == sd.EXPECTED_KEYS and all(b"device:" in a for a, _ in changed)  # comment + dòng khác giữ nguyên
    assert sd.apply_device(other, cfg) == 0  # đã đúng -> không ghi lại

    assert sd.apply_device(start.pop(), cfg) == sd.EXPECTED_KEYS
    assert cfg.read_bytes() == src  # trả về đúng từng byte (kể cả kiểu xuống dòng)


def test_wrong_number_of_device_keys_is_an_error(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_bytes(b'app:\n  device: "cuda"\n')
    with pytest.raises(RuntimeError, match="device"):
        sd.apply_device("cpu", cfg)
    assert cfg.read_bytes() == b'app:\n  device: "cuda"\n'
    assert sd.main(["cpu", "--config", str(cfg)]) == 1
