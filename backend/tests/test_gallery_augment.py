"""Test cân bằng gallery lúc lập index (``retrieval.augment``) và cắt ảnh gallery (``retrieval.gallery_crop``)."""

from __future__ import annotations

import faiss
import numpy as np
import pytest

from engine.catalog.factory import open_catalog_repository
from engine.core.config import GalleryAugmentSection
from engine.pipeline.build import BuildPipeline
from engine.retrieval.backends.mock_visual_embedding import MockVisualEmbeddingBackend
from engine.retrieval.fingerprint import compute_fingerprint
from engine.retrieval.gallery_builder import (
    GalleryIndexBuilder,
    augment_views,
    expected_vectors,
    plan_views,
    select_diverse,
)


def test_augment_views_follow_rotation_order():
    image = np.zeros((40, 60, 3), dtype=np.uint8)
    image[0, 0] = 255  # góc trên-trái để kiểm chiều xoay
    views = augment_views(image, [90, 180])

    assert [v.shape[:2] for v in views] == [(60, 40), (40, 60)]
    assert views[0][0, -1].tolist() == [255, 255, 255]  # xoay 90 độ theo chiều kim đồng hồ
    assert views[1][-1, -1].tolist() == [255, 255, 255]


def test_plan_views_fills_thin_skus_up_to_target():
    counts = {"a": 2, "b": 5, "c": 12, "d": 30}
    augment = GalleryAugmentSection(enabled=True, rotations=[90, 270, 180], target_vectors=12)

    assert plan_views(counts, augment) == {"a": 4, "b": 3, "c": 1, "d": 1}  # a bị kẹp ở 1 + 3 góc
    assert plan_views(counts, GalleryAugmentSection(target_vectors=12)) == {"a": 1, "b": 1, "c": 1, "d": 1}


def test_plan_views_auto_target_is_median_image_count():
    counts = {"a": 2, "b": 4, "c": 8, "d": 20, "e": 30}
    augment = GalleryAugmentSection(enabled=True, rotations=[90, 270, 180])  # target "auto" = 8

    assert plan_views(counts, augment) == {"a": 4, "b": 2, "c": 1, "d": 1, "e": 1}


def test_select_diverse_drops_near_duplicates():
    vectors = np.array([[1.0, 0.0], [0.999, 0.045], [0.0, 1.0], [0.7071, 0.7071]], dtype=np.float32)
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)

    assert select_diverse(vectors, 2) == [0, 2]
    assert select_diverse(vectors, 3) == [0, 2, 3]
    assert select_diverse(vectors, 9) == [0, 1, 2, 3]


def test_expected_vectors_respects_max_vectors():
    capped = GalleryAugmentSection(enabled=True, max_vectors=10)

    assert expected_vectors(4, 3, capped) == 10
    assert expected_vectors(2, 3, capped) == 6
    assert expected_vectors(40, 1, GalleryAugmentSection(max_vectors=10)) == 40  # tắt = như cũ


def test_invalid_augment_values_are_rejected():
    with pytest.raises(ValueError):
        GalleryAugmentSection(target_vectors=0)
    with pytest.raises(ValueError):
        GalleryAugmentSection(rotations=[90, 90])


def _with_retrieval(cfg, **update):
    return cfg.model_copy(update={"retrieval": cfg.retrieval.model_copy(update=update)})


def _index_size(cfg) -> int:
    return faiss.read_index(str(cfg.resolve_path(cfg.retrieval.gallery_index_path))).ntotal


def test_build_balances_vectors_and_changes_fingerprint(gallery_config):
    mapping = open_catalog_repository(gallery_config).folder_to_product_id()
    plain = compute_fingerprint(gallery_config, mapping)["digest"]

    cfg = _with_retrieval(gallery_config, augment=GalleryAugmentSection(enabled=True, target_vectors=3))
    assert compute_fingerprint(cfg, mapping)["digest"] != plain
    off = _with_retrieval(gallery_config, augment=GalleryAugmentSection(enabled=False, target_vectors=3))
    assert compute_fingerprint(off, mapping)["digest"] == plain

    BuildPipeline(cfg).run()
    assert _index_size(cfg) == 2 * 3  # 2 SKU x 1 ảnh x (gốc + 2 góc xoay)

    capped = _with_retrieval(gallery_config, augment=GalleryAugmentSection(enabled=True, target_vectors=4, max_vectors=2))
    BuildPipeline(capped).run()
    assert _index_size(capped) == 2 * 2


def test_gallery_crop_uses_cropper_and_needs_one(gallery_config):
    cfg = _with_retrieval(gallery_config, gallery_crop="detector")
    mapping = open_catalog_repository(gallery_config).folder_to_product_id()
    assert compute_fingerprint(cfg, mapping)["digest"] != compute_fingerprint(gallery_config, mapping)["digest"]

    backend = MockVisualEmbeddingBackend(cfg.retrieval)
    with pytest.raises(ValueError):
        GalleryIndexBuilder(cfg, backend)

    seen: list[tuple[int, int]] = []

    def cropper(image: np.ndarray) -> np.ndarray | None:
        seen.append(image.shape[:2])
        return image[10:90, 10:90] if len(seen) == 1 else None  # ảnh thứ hai: không thấy sản phẩm

    index, metadata = GalleryIndexBuilder(cfg, backend, cropper=cropper).build()
    assert len(seen) == 2 and index.ntotal == len(metadata) == 2
