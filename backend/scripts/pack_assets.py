"""Đóng gói weights + dữ liệu để phát hành công khai (GitHub Release), kèm manifest SHA-256.

    python scripts/pack_assets.py --db <app.db đã làm sạch> --out <thư mục ngoài repo> [--catalog-dump <dump sạch>]

Từ backend/. Tạo trong ``--out``:
    stocktaking_weights_detector_sam2.zip   weights/detector + weights/refinement/sam2
    stocktaking_weights_siglip2.zip         weights/retriever/siglip2
    stocktaking_data.zip                    gallery, hai bộ benchmark, catalog SQLite, cache, baseline, demo_sets
    SHA256SUMS.txt                          mã kiểm của từng file zip
và ghi ``configs/assets_manifest.json`` (đường dẫn, kích thước, SHA-256 của từng file đã đóng gói)
để ``scripts/verify_manifest.py`` kiểm sau khi giải nén.

``--db`` là bản catalog SQLite KHÔNG có tài khoản/đơn hàng (repo công khai): file ``data/db/app.db``
trên máy dev thường chứa cả dữ liệu web v1. Không đóng gói: data/experiments, data/outputs,
data/transactions (ảnh giao dịch), các thư mục *_inbox, ảnh gốc trước khi cắt, bản sao lưu nhãn.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
MANIFEST = BACKEND / "configs" / "assets_manifest.json"
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
SKIP_NAMES = {".gitkeep", ".DS_Store", "Thumbs.db"}


def _files(root: Path, patterns: list[str]) -> list[Path]:
    out: list[Path] = []
    for pattern in patterns:
        out.extend(p for p in sorted(root.glob(pattern)) if p.is_file() and p.name not in SKIP_NAMES)
    return out


def bundles(db: Path) -> dict[str, list[tuple[Path, str]]]:
    """Tên zip -> [(file nguồn, đường dẫn trong zip tính từ backend/)]."""

    def rel(paths: list[Path]) -> list[tuple[Path, str]]:
        return [(p, p.relative_to(BACKEND).as_posix()) for p in paths]

    data = rel(
        _files(BACKEND, [
            "data/gallery/*/*",
            "data/benchmark/_annotations.coco.json", "data/benchmark/products.json", "data/benchmark/images/*",
            "data/benchmark_new/_annotations.coco.json", "data/benchmark_new/_labels.json", "data/benchmark_new/images/*",
            "data/cache/gallery_index.faiss", "data/cache/gallery_index.faiss.fingerprint.json",
            "data/cache/gallery_metadata.json", "data/cache/color_signatures.npz",
            "data/baseline/main_1008_new/*", "data/baseline/main_1008_val/*",
            "data/demo_sets/*", "data/demo_sets/*/*",
        ])
    ) + [(db, "data/db/app.db")]
    return {
        "stocktaking_weights_detector_sam2.zip": rel(_files(BACKEND, ["weights/detector/*", "weights/refinement/sam2/*"])),
        "stocktaking_weights_siglip2.zip": rel(_files(BACKEND, ["weights/retriever/siglip2/*"])),
        "stocktaking_data.zip": data,
    }


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Đóng gói weights + dữ liệu để phát hành công khai.")
    parser.add_argument("--db", type=Path, required=True, help="catalog SQLite đã làm sạch (không tài khoản/đơn hàng)")
    parser.add_argument("--out", type=Path, required=True, help="thư mục ghi các file zip (nên ở ngoài repo)")
    parser.add_argument(
        "--catalog-dump", type=Path, help="bản pg_dump chỉ có catalog + giá (không tài khoản/đơn); thêm vào SHA256SUMS"
    )
    args = parser.parse_args(argv)
    if not args.db.is_file():
        print(f"Không thấy {args.db}", file=sys.stderr)
        return 1
    args.out.mkdir(parents=True, exist_ok=True)

    manifest: dict[str, list[dict]] = {"weights": [], "data": []}
    sums = []
    for zip_name, members in bundles(args.db).items():
        target = args.out / zip_name
        if target.exists():
            print(f"Dừng: {target} đã tồn tại (không ghi đè).", file=sys.stderr)
            return 1
        group = "weights" if "weights" in zip_name else "data"
        with zipfile.ZipFile(target, "w", allowZip64=True) as archive:
            for source, arcname in members:
                stored = source.suffix.lower() in IMAGE_SUFFIXES or source.suffix in {".pth", ".pt", ".safetensors", ".faiss"}
                archive.write(source, arcname, compress_type=zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED)
                manifest[group].append({"path": arcname, "size": source.stat().st_size, "sha256": sha256_of(source)})
        sums.append(f"{sha256_of(target)}  {zip_name}")
        print(f"{zip_name}: {len(members)} file, {target.stat().st_size / 2**20:.0f} MB")
    if args.catalog_dump is not None:
        # GitHub Release không nhận đuôi .dump: phát hành trong một file zip
        packed = args.out / "stocktaking_catalog.zip"
        with zipfile.ZipFile(packed, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.write(args.catalog_dump, "stocktaking_catalog.dump")
        sums.append(f"{sha256_of(packed)}  {packed.name}")
    for group in manifest.values():
        group.sort(key=lambda item: item["path"])
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (args.out / "SHA256SUMS.txt").write_text("\n".join(sums) + "\n", encoding="utf-8")
    print(f"Manifest: {MANIFEST} ({len(manifest['weights'])} file weights, {len(manifest['data'])} file dữ liệu)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
