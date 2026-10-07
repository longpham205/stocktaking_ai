"""Test augment ảnh gallery lúc lập index (``retrieval.augment``)."""

from __future__ import annotations

import faiss
import numpy as np
import pytest

from engine.catalog.factory import open_catalog_repository
from engine.core.config import GalleryAugmentSection
from engine.pipeline.build import BuildPipeline
from engine.retrieval.fingerprint import compute_fingerprint
from engine.retrieval.gallery_builder import augment_views, views_per_image


def test_augment_views_rotations_then_center_crops():
    image = np.zeros((40, 60, 3), dtype=np.uint8)
    image[0, 0] = 255  # góc trên-trái để kiểm chiều xoay
    views = augment_views(image, GalleryAugmentSection(enabled=True, rotations=[90, 180], center_crops=[0.5]))

    assert [v.shape[:2] for v in views] == [(60, 40), (40, 60), (20, 30)]
    assert views[0][0, -1].tolist() == [255, 255, 255]  # xoay 90 độ theo chiều kim đồng hồ
    assert views[1][-1, -1].tolist() == [255, 255, 255]


def test_views_per_image_respects_max_images():
    augment = GalleryAugmentSection(enabled=True, rotations=[90, 180, 270], center_crops=[0.8], max_images=5)

    assert views_per_image(augment, 5) == 5
    assert views_per_image(augment, 6) == 1
    assert views_per_image(GalleryAugmentSection(), 3) == 1  # tắt = như cũ


def test_center_crop_ratio_must_be_inside_unit_interval():
    with pytest.raises(ValueError):
        GalleryAugmentSection(center_crops=[1.0])


def _with_augment(cfg, **augment):
    retrieval = cfg.retrieval.model_copy(update={"augment": GalleryAugmentSection(**augment)})
    return cfg.model_copy(update={"retrieval": retrieval})


def test_build_adds_augmented_vectors_and_changes_fingerprint(gallery_config):
    mapping = open_catalog_repository(gallery_config).folder_to_product_id()
    plain = compute_fingerprint(gallery_config, mapping)

    cfg = _with_augment(gallery_config, enabled=True, rotations=[90, 180, 270])
    assert compute_fingerprint(cfg, mapping)["digest"] != plain["digest"]
    assert compute_fingerprint(_with_augment(gallery_config, enabled=False), mapping)["digest"] == plain["digest"]

    BuildPipeline(cfg).run()
    index = faiss.read_index(str(cfg.resolve_path(cfg.retrieval.gallery_index_path)))
    assert index.ntotal == 2 * 4  # 2 ảnh gốc x (gốc + 3 góc xoay)
