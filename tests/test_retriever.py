"""Unit tests for src.retrieval.retriever.Retriever and gallery_builder."""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from src.core.utils import generate_id
from src.models.models import BoundingBox, CropImage
from src.retrieval.retriever import Retriever


def _make_crop(raw_image_array: np.ndarray) -> CropImage:
    """Build a CropImage with separate raw and retrieval representations.

    Retrieval receives the fixed-size ``image_array`` while the original
    resolution is preserved in ``raw_image_array`` for downstream plugins.
    """
    height, width = raw_image_array.shape[:2]

    image_array = cv2.resize(
        raw_image_array,
        (224, 224),
        interpolation=cv2.INTER_AREA,
    )

    return CropImage(
        crop_id=generate_id(),
        image_id="img",
        image_array=image_array,
        raw_image_array=raw_image_array,
        source_bbox=BoundingBox(0, 0, width, height),
        detection_confidence=0.9,
        detection_index=0,
        used_refined_bbox=False,
    )


def test_retriever_builds_from_gallery_config(gallery_config) -> None:
    """Retriever must load the pre-built index produced by BuildPipeline."""
    retriever = Retriever(gallery_config)

    assert retriever._index.ntotal == 2  # noqa: SLF001 - white-box check for test only


def test_retriever_matches_red_crop_to_product_id_1(gallery_config) -> None:
    """A crop visually matching the red gallery product must resolve to product_id '1'."""
    retriever = Retriever(gallery_config)

    red_crop_array = np.full(
        (100, 100, 3),
        255,
        dtype=np.uint8,
    )
    red_crop_array[10:90, 10:90] = (30, 30, 200)

    crop = _make_crop(red_crop_array)

    result = retriever.retrieve(crop)

    assert result.top_candidate is not None
    assert result.top_candidate.product_id == "1"
    assert result.top_candidate.product_name == "prod_red_square"
    assert result.detection_confidence == 0.9


def test_retriever_get_product_returns_catalog_entry(gallery_config) -> None:
    """get_product must resolve a product_id to its full catalog dict."""
    retriever = Retriever(gallery_config)

    product = retriever.get_product("2")

    assert product is not None
    assert product["product_name"] == "prod_blue_square"
    assert product["barcode"] == "1234567890"  # set by the gallery_config fixture


def test_retriever_get_product_unknown_returns_none(gallery_config) -> None:
    """get_product must return None for an unknown product_id."""
    retriever = Retriever(gallery_config)

    assert retriever.get_product("999") is None


def test_retriever_raises_without_built_index(test_config) -> None:
    """Retriever must raise FileNotFoundError if the gallery was never built."""
    with pytest.raises(FileNotFoundError):
        Retriever(test_config)


def test_retriever_candidates_ranked_descending(gallery_config) -> None:
    """Retrieval candidates must be ranked with increasing rank index."""
    crop_array = np.full(
        (100, 100, 3),
        255,
        dtype=np.uint8,
    )
    crop_array[10:90, 10:90] = (30, 30, 200)

    crop = _make_crop(crop_array)

    retriever = Retriever(gallery_config)

    result = retriever.retrieve(crop)

    ranks = [candidate.rank for candidate in result.candidates]

    assert ranks == sorted(ranks)

    scores = [candidate.similarity_score for candidate in result.candidates]

    assert scores == sorted(scores, reverse=True)
    
def test_retriever_crop_keeps_separate_raw_and_resized_images() -> None:
    """CropImage must preserve raw resolution separately from retrieval input."""
    raw_image = np.zeros(
        (480, 320, 3),
        dtype=np.uint8,
    )

    crop = _make_crop(raw_image)

    assert crop.raw_image_array.shape == (480, 320, 3)
    assert crop.image_array.shape == (224, 224, 3)
    assert crop.source_bbox == BoundingBox(0, 0, 320, 480)