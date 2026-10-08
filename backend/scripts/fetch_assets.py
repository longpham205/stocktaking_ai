"""Tải weights + dữ liệu đã phát hành (GitHub Release), kiểm SHA-256, giải nén vào backend/.

    python scripts/fetch_assets.py                     # từ backend/: tải bản phát hành mặc định
    python scripts/fetch_assets.py --from-dir D:/tai   # đã có sẵn các file zip (tải tay / Google Drive)
    python scripts/fetch_assets.py --only weights      # chỉ weights (hoặc: --only data)

Mỗi file zip được so với SHA256SUMS.txt của bản phát hành trước khi giải nén; sau khi giải nén, từng
file được so với ``configs/assets_manifest.json``. File đã có trên máy không bị ghi đè (thêm ``--force``
để ghi đè). Catalog cho web (Postgres): ``stocktaking_catalog.zip`` (GitHub Release không nhận đuôi .dump)
được giải nén thành ``backups/stocktaking_catalog.dump`` ở gốc repo, nạp bằng ``make db-restore NAME=stocktaking_catalog`` rồi tạo tài khoản bằng
``make reset-password USER_NAME=admin ROLE=admin``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
MANIFEST = BACKEND / "configs" / "assets_manifest.json"
RELEASE_URL = "https://github.com/longpham205/stocktaking_ai/releases/download/{tag}/{name}"
DEFAULT_TAG = "assets-v1"
ZIPS = {
    "weights": ["stocktaking_weights_detector_sam2.zip", "stocktaking_weights_siglip2.zip"],
    "data": ["stocktaking_data.zip"],
}
CATALOG_ZIP = "stocktaking_catalog.zip"
CATALOG_DUMP = "stocktaking_catalog.dump"


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def obtain(name: str, tag: str, from_dir: Path | None, cache: Path) -> Path:
    """Đường dẫn tới file ``name``: lấy từ ``from_dir`` hoặc tải về ``cache``."""
    if from_dir is not None:
        path = from_dir / name
        if not path.is_file():
            raise FileNotFoundError(f"Không thấy {path}")
        return path
    target = cache / name
    if not target.is_file():
        url = RELEASE_URL.format(tag=tag, name=name)
        print(f"Tải {url}")
        partial = target.with_suffix(target.suffix + ".part")
        with urllib.request.urlopen(url) as response, partial.open("wb") as handle:
            shutil.copyfileobj(response, handle, length=1 << 20)
        partial.replace(target)
    return target


def expected_sums(tag: str, from_dir: Path | None, cache: Path) -> dict[str, str]:
    text = obtain("SHA256SUMS.txt", tag, from_dir, cache).read_text(encoding="utf-8")
    return {name: digest for digest, name in (line.split(None, 1) for line in text.splitlines() if line.strip())}


def extract(archive_path: Path, force: bool) -> tuple[int, int]:
    """Giải nén vào backend/; trả (số file ghi, số file bỏ qua vì đã có)."""
    written = skipped = 0
    with zipfile.ZipFile(archive_path) as archive:
        for member in archive.infolist():
            if member.is_dir():
                continue
            target = (BACKEND / member.filename).resolve()
            if BACKEND.resolve() not in target.parents:
                raise ValueError(f"Đường dẫn lạ trong zip: {member.filename}")
            if target.exists() and not force:
                skipped += 1
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, target.open("wb") as handle:
                shutil.copyfileobj(source, handle, length=1 << 20)
            written += 1
    return written, skipped


def verify(groups: list[str]) -> int:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    bad = 0
    for group in groups:
        for entry in manifest.get(group, []):
            path = BACKEND / entry["path"]
            if not path.is_file() or path.stat().st_size != entry["size"] or sha256_of(path) != entry["sha256"]:
                print(f"  KHÁC manifest: {entry['path']}")
                bad += 1
        print(f"{group}: {len(manifest.get(group, []))} file, {bad} file khác manifest")
    return bad


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Tải và giải nén weights + dữ liệu đã phát hành.")
    parser.add_argument("--tag", default=DEFAULT_TAG, help=f"tag của GitHub Release (mặc định {DEFAULT_TAG})")
    parser.add_argument("--from-dir", type=Path, help="thư mục đã có sẵn các file zip, không tải")
    parser.add_argument("--only", choices=sorted(ZIPS), help="chỉ một nhóm")
    parser.add_argument("--force", action="store_true", help="ghi đè file đã có")
    args = parser.parse_args(argv)

    cache = REPO / "backups" / "assets"
    cache.mkdir(parents=True, exist_ok=True)
    groups = [args.only] if args.only else list(ZIPS)
    sums = expected_sums(args.tag, args.from_dir, cache)
    for group in groups:
        for name in ZIPS[group]:
            path = obtain(name, args.tag, args.from_dir, cache)
            if sha256_of(path) != sums.get(name):
                print(f"Dừng: {name} sai mã SHA-256 (tải lỗi hoặc khác bản phát hành).", file=sys.stderr)
                return 1
            written, skipped = extract(path, args.force)
            print(f"{name}: giải nén {written} file, bỏ qua {skipped} file đã có")
    if "data" in groups:
        packed = obtain(CATALOG_ZIP, args.tag, args.from_dir, cache)
        if sha256_of(packed) != sums.get(CATALOG_ZIP):
            print(f"Dừng: {CATALOG_ZIP} sai mã SHA-256.", file=sys.stderr)
            return 1
        with zipfile.ZipFile(packed) as archive, archive.open(CATALOG_DUMP) as source:
            (REPO / "backups" / CATALOG_DUMP).write_bytes(source.read())
        print(f"Catalog web: backups/{CATALOG_DUMP} -> make db-restore NAME=stocktaking_catalog")
    return 1 if verify(groups) else 0


if __name__ == "__main__":
    raise SystemExit(main())
