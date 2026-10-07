"""Dấu vân tay (fingerprint) của gallery index: build lại FAISS CHỈ khi đầu vào thật sự đổi.

Thành phần: nội dung ảnh gallery (hash từng file) + ánh xạ thư mục -> product_id + backend
embedding (tên, model, weights) + ``embedding_dim`` + ``retrieval.augment`` / ``retrieval.gallery_crop`` (khi bật). Lưu cạnh index ở
``<gallery_index_path>.fingerprint.json`` để không đổi định dạng ``gallery_metadata.json``.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from engine.core.utils import list_image_files

if TYPE_CHECKING:
    from engine.core.config import AppConfig

FINGERPRINT_FORMAT = 1


def fingerprint_path(config: "AppConfig") -> Path:
    index_path = config.resolve_path(config.retrieval.gallery_index_path)
    return index_path.with_name(index_path.name + ".fingerprint.json")


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _model_identity(config: "AppConfig") -> dict[str, Any]:
    r = config.retrieval
    ident: dict[str, Any] = {"backend": r.backend, "embedding_dim": r.embedding_dim}
    if r.backend == "siglip2":
        ident["model_name"] = r.siglip2.model_name
        weights = config.resolve_path(r.siglip2.weights_path) if r.siglip2.weights_path else None
        if weights is not None and weights.exists():
            files = [weights] if weights.is_file() else sorted(p for p in weights.rglob("*") if p.is_file())
            ident["weights"] = [[p.relative_to(weights).as_posix() if p != weights else p.name, p.stat().st_size] for p in files]
    elif r.backend == "mock_visual_embedding":
        ident["color_hist_bins"] = r.color_hist_bins
    return ident


def compute_fingerprint(config: "AppConfig", folder_to_product_id: dict[str, str]) -> dict[str, Any]:
    gallery_dir = config.resolve_path(config.paths.gallery_dir)
    images: dict[str, str] = {}
    for folder in sorted(folder_to_product_id):
        d = gallery_dir / folder
        if not d.is_dir():
            continue
        for img in list_image_files(d):
            images[f"{folder}/{img.name}"] = _hash_file(img)
    payload = {
        "format": FINGERPRINT_FORMAT,
        "model": _model_identity(config),
        "mapping": dict(sorted(folder_to_product_id.items())),
        "images": dict(sorted(images.items())),
    }
    # Chỉ thêm khi bật: tắt thì giữ nguyên digest cũ (không build lại vô cớ).
    if config.retrieval.augment.enabled:
        payload["augment"] = config.retrieval.augment.model_dump()
    if config.retrieval.gallery_crop != "none":
        payload["gallery_crop"] = {
            "mode": config.retrieval.gallery_crop,
            "padding_pixels": config.cropping.padding_pixels,
            "detection": config.detection.model_dump(mode="json"),
        }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    return {"format": FINGERPRINT_FORMAT, "digest": digest, "model": payload["model"], "image_count": len(images)}


def read_fingerprint(config: "AppConfig") -> dict[str, Any] | None:
    path = fingerprint_path(config)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def write_fingerprint(config: "AppConfig", fp: dict[str, Any]) -> Path:
    path = fingerprint_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(fp, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def index_is_current(config: "AppConfig", fp: dict[str, Any]) -> bool:
    """True khi index + metadata tồn tại và fingerprint đã lưu trùng ``fp``."""
    index = config.resolve_path(config.retrieval.gallery_index_path)
    meta = config.resolve_path(config.retrieval.gallery_metadata_path)
    saved = read_fingerprint(config)
    return index.is_file() and meta.is_file() and saved is not None and saved.get("digest") == fp["digest"]


__all__ = ["compute_fingerprint", "fingerprint_path", "index_is_current", "read_fingerprint", "write_fingerprint"]
