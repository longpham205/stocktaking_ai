"""Verify local files against assets_manifest.json.

Checks every file listed under a given group ("weights" or "data")
exists with the expected size (and sha256 if --hash passed). Prints
missing/mismatched files. Exit code 0 = everything matches, 1 = not.

Usage:
    python scripts/verify_manifest.py --group weights
    python scripts/verify_manifest.py --group data --hash   # slower, thorough
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_FILE = PROJECT_ROOT / "assets_manifest.json"


def sha256_of(path: Path, chunk_size: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--group", required=True, choices=["weights", "data"])
    parser.add_argument("--hash", action="store_true", help="Also verify sha256 (slower).")
    args = parser.parse_args()

    if not MANIFEST_FILE.exists():
        print(f"ERROR: {MANIFEST_FILE.name} not found.")
        return 1

    entries = json.loads(MANIFEST_FILE.read_text(encoding="utf-8")).get(args.group, [])
    if not entries:
        print(f"WARNING: manifest has no entries for group '{args.group}'.")
        return 0

    missing, size_mismatch, hash_mismatch = [], [], []
    for entry in entries:
        path = PROJECT_ROOT / entry["path"]
        if not path.exists():
            missing.append(entry["path"]); continue
        if path.stat().st_size != entry["size"]:
            size_mismatch.append(entry["path"]); continue
        if args.hash and sha256_of(path) != entry["sha256"]:
            hash_mismatch.append(entry["path"])

    total = len(entries)
    ok = total - len(missing) - len(size_mismatch) - len(hash_mismatch)
    print(f"[{args.group}] {ok}/{total} file(s) OK.")
    for label, items in (("Missing", missing), ("Size mismatch", size_mismatch), ("Hash mismatch", hash_mismatch)):
        if items:
            print(f"  {label} ({len(items)}):")
            for p in items[:20]:
                print(f"    - {p}")
            if len(items) > 20:
                print(f"    ... and {len(items) - 20} more")

    return 0 if ok == total else 1


if __name__ == "__main__":
    sys.exit(main())