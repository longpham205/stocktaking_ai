"""Test fingerprint gallery: BuildPipeline chỉ build lại FAISS khi đầu vào đổi (C9/C10)."""

from __future__ import annotations

import cv2
import numpy as np

from src.pipeline.build import BuildPipeline
from src.retrieval.fingerprint import fingerprint_path, read_fingerprint


def _index_mtime(cfg) -> int:
    return cfg.resolve_path(cfg.retrieval.gallery_index_path).stat().st_mtime_ns


def test_fingerprint_written_and_rebuild_only_on_change(gallery_config):
    cfg = gallery_config
    assert fingerprint_path(cfg).is_file()
    fp1 = read_fingerprint(cfg)
    assert fp1["image_count"] == 2

    before = _index_mtime(cfg)
    BuildPipeline(cfg).run()  # không đổi gì -> bỏ qua
    assert _index_mtime(cfg) == before
    assert read_fingerprint(cfg)["digest"] == fp1["digest"]

    img_path = cfg.resolve_path(cfg.paths.gallery_dir) / "prod_red_square" / "02.png"
    cv2.imwrite(str(img_path), np.full((100, 100, 3), 90, dtype=np.uint8))
    BuildPipeline(cfg).run()  # thêm ảnh -> build lại
    fp2 = read_fingerprint(cfg)
    assert fp2["digest"] != fp1["digest"] and fp2["image_count"] == 3


def test_missing_index_forces_rebuild(gallery_config):
    cfg = gallery_config
    cfg.resolve_path(cfg.retrieval.gallery_index_path).unlink()
    BuildPipeline(cfg).run()
    assert cfg.resolve_path(cfg.retrieval.gallery_index_path).is_file()


def test_model_change_changes_fingerprint(gallery_config):
    from src.catalog.factory import open_catalog_repository
    from src.retrieval.fingerprint import compute_fingerprint

    cfg = gallery_config
    mapping = open_catalog_repository(cfg).folder_to_product_id()
    a = compute_fingerprint(cfg, mapping)
    cfg2 = cfg.model_copy(update={"retrieval": cfg.retrieval.model_copy(update={"color_hist_bins": cfg.retrieval.color_hist_bins + 1})})
    assert compute_fingerprint(cfg2, mapping)["digest"] != a["digest"]
    assert compute_fingerprint(cfg, {k: v for k, v in mapping.items() if v != "1"})["digest"] != a["digest"]
