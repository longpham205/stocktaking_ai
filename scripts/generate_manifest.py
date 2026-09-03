"""Generate assets_manifest.json (path, size, sha256) for weights/ and data/.

Run this ONCE, locally, from a machine that has the correct/complete
weights/ and data/ folders — BEFORE zipping them for Google Drive.
Commit the resulting assets_manifest.json to git: it's small text,
safe to version, and is what setup_e2e.sh uses to verify downloads
without any hardcoded file list in bash.

Usage:
    python scripts/generate_manifest.py
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_FILE = PROJECT_ROOT / "assets_manifest.json"

# Which directories/files belong to each group. Edit if the layout changes.
GROUPS = {
    "weights": [PROJECT_ROOT / "weights"],
    "data": [
        PROJECT_ROOT / "data" / "gallery",
        PROJECT_ROOT / "data" / "metadata",
        PROJECT_ROOT / "data" / "cache" / "gallery_index.faiss",
        PROJECT_ROOT / "data" / "cache" / "gallery_metadata.json",
        PROJECT_ROOT / "data" / "benchmark",
    ],
}

EXCLUDE_NAMES = {".gitkeep", ".DS_Store"}
EXCLUDE_SUFFIXES = {".log"}


def sha256_of(path: Path, chunk_size: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()


def iter_files(root: Path):
    if root.is_file():
        yield root
    elif root.is_dir():
        for p in sorted(root.rglob("*")):
            if p.is_file() and p.name not in EXCLUDE_NAMES and p.suffix not in EXCLUDE_SUFFIXES:
                yield p


def build_group(paths: list[Path]) -> list[dict]:
    entries = []
    for root in paths:
        if not root.exists():
            print(f"  [skip] not found: {root.relative_to(PROJECT_ROOT)}")
            continue
        for f in iter_files(root):
            entries.append({
                "path": f.relative_to(PROJECT_ROOT).as_posix(),
                "size": f.stat().st_size,
                "sha256": sha256_of(f),
            })
    return entries


def main() -> None:
    manifest = {}
    for name, paths in GROUPS.items():
        print(f"Scanning group '{name}' ...")
        manifest[name] = build_group(paths)
        print(f"  -> {len(manifest[name])} file(s)")

    OUTPUT_FILE.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nWrote {OUTPUT_FILE.relative_to(PROJECT_ROOT)} ")


if __name__ == "__main__":
    main()