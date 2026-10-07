"""Test cân bằng index gallery lúc lập index (``retrieval.augment``)."""

from __future__ import annotations

import faiss
import numpy as np
import pytest

from engine.catalog.factory import open_catalog_repository
from engine.core.config import GalleryAugmentSection
from engine.pipeline.build import BuildPipeline
from engine.retrieval.fingerprint import compute_fingerprint
from engine.retrieval.gallery_builder import augment_views, expected_vectors, plan_views, select_diverse


def test_augment_views_follow_rotation_order():
    image = np.zeros((40, 60, 3), dtype=np.uint8)
    image[0, 0] = 255  # góc trên-trái để kiểm chiều xoay
    views = augment_views(image, [90, 180])

    assert [v.shape[:2] for v in views] == [(60, 40), (40, 60)]
    assert views[0][0, -1].tolist() == [255, 255, 255]  # xoay 90 độ theo chiều kim đồng hồ
    assert views[1][-1, -1].tolist() == [255, 255, 255]


def test_plan_views_rotates_only_thin_skus_with_every_angle():
    counts = {"a": 2, "b": 9, "c": 10, "d": 30}

    assert plan_views(counts, GalleryAugmentSection(enabled=True, max_images=9)) == {"a": 4, "b": 4, "c": 1, "d": 1}
    assert plan_views(counts, GalleryAugmentSection(enabled=True)) == {"a": 4, "b": 4, "c": 4, "d": 4}
    assert plan_views(counts, GalleryAugmentSection(max_images=9)) == {"a": 1, "b": 1, "c": 1, "d": 1}


def test_select_diverse_drops_near_duplicates():
    vectors = np.array([[1.0, 0.0], [0.999, 0.045], [0.0, 1.0], [0.7071, 0.7071]], dtype=np.float32)
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)

    assert select_diverse(vectors, 2) == [0, 2]
    assert select_diverse(vectors, 3) == [0, 2, 3]
    assert select_diverse(vectors, 9) == [0, 1, 2, 3]


def test_expected_vectors_respects_max_vectors():
    capped = GalleryAugmentSection(enabled=True, max_vectors=10)

    assert expected_vectors(4, 4, capped) == 10
    assert expected_vectors(2, 4, capped) == 8
    assert expected_vectors(40, 1, GalleryAugmentSection(max_vectors=10)) == 40  # tắt = như cũ


def test_duplicate_rotations_are_rejected():
    with pytest.raises(ValueError):
        GalleryAugmentSection(rotations=[90, 90])


def _with_augment(cfg, **augment):
    return cfg.model_copy(update={"retrieval": cfg.retrieval.model_copy(update={"augment": GalleryAugmentSection(**augment)})})


def _index_size(cfg) -> int:
    return faiss.read_index(str(cfg.resolve_path(cfg.retrieval.gallery_index_path))).ntotal


def test_build_adds_rotations_caps_vectors_and_changes_fingerprint(gallery_config):
    mapping = open_catalog_repository(gallery_config).folder_to_product_id()
    plain = compute_fingerprint(gallery_config, mapping)["digest"]

    cfg = _with_augment(gallery_config, enabled=True, max_images=9)
    assert compute_fingerprint(cfg, mapping)["digest"] != plain
    assert compute_fingerprint(_with_augment(gallery_config, max_images=9), mapping)["digest"] == plain

    BuildPipeline(cfg).run()
    assert _index_size(cfg) == 2 * 4  # 2 SKU x 1 ảnh x (gốc + 3 góc xoay)

    capped = _with_augment(gallery_config, enabled=True, max_images=9, max_vectors=2)
    BuildPipeline(capped).run()
    assert _index_size(capped) == 2 * 2
