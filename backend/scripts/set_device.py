"""Đổi thiết bị chạy model (cpu/cuda) trong configs/config.yaml.

    python scripts/set_device.py show     # in các khoá device hiện tại
    python scripts/set_device.py cpu      # máy không có GPU
    python scripts/set_device.py cuda

Chỉ dùng thư viện chuẩn: chạy được TRƯỚC khi PyYAML được cài (trước đây `setup.py` và `bin/setup_colab.sh` gọi script này; nay ở src_legacy/).
Chỉ sửa giá trị trên các dòng `device:` (giữ nguyên comment, thứ tự, kiểu xuống dòng). Số dòng `device:`
khác EXPECTED_KEYS thì dừng và báo lỗi, tránh vá thiếu âm thầm khi config thêm/mất khoá.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "configs" / "config.yaml"
EXPECTED_KEYS = 5  # app, detection.rf_detr, refinement.sam2, retrieval.siglip2, plugins.ocr
_LINE = re.compile(r'^(\s*device:\s*)(["\']?)(cpu|cuda)(["\']?)(.*)$', re.DOTALL)


def read_devices(config: Path = DEFAULT_CONFIG) -> list[tuple[int, str]]:
    """[(số dòng, giá trị)] của mọi dòng `device:` trong file."""
    lines = Path(config).read_bytes().decode("utf-8").splitlines(keepends=True)
    return [(i + 1, m.group(3)) for i, line in enumerate(lines) if (m := _LINE.match(line))]


def apply_device(mode: str, config: Path = DEFAULT_CONFIG) -> int:
    """Đặt mọi khoá `device:` thành `mode`; trả về số dòng đã đổi."""
    if mode not in ("cpu", "cuda"):
        raise ValueError(f"device phải là cpu hoặc cuda, nhận: {mode!r}")
    config = Path(config)
    lines = config.read_bytes().decode("utf-8").splitlines(keepends=True)
    hits = [i for i, line in enumerate(lines) if _LINE.match(line)]
    if len(hits) != EXPECTED_KEYS:
        raise RuntimeError(f"{config.name}: tìm thấy {len(hits)} dòng device:, cần đúng {EXPECTED_KEYS} — kiểm tra lại config trước khi đổi thiết bị")
    changed = 0
    for i in hits:
        m = _LINE.match(lines[i])
        if m.group(3) != mode:
            lines[i] = f"{m.group(1)}{m.group(2)}{mode}{m.group(4)}{m.group(5)}"
            changed += 1
    if changed:
        config.write_bytes("".join(lines).encode("utf-8"))
    return changed


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Đổi thiết bị chạy model trong config.")
    parser.add_argument("mode", choices=["cpu", "cuda", "show"])
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args(argv)
    if not args.config.is_file():
        print(f"LỖI: không thấy {args.config}", file=sys.stderr)
        return 1
    if args.mode != "show":
        try:
            n = apply_device(args.mode, args.config)
        except RuntimeError as exc:
            print(f"LỖI: {exc}", file=sys.stderr)
            return 1
        print(f"{args.config.name}: đã đổi {n} dòng sang {args.mode}")
    for no, value in read_devices(args.config):
        print(f"  dòng {no}: device = {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
