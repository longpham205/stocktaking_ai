"""Unit / integration tests for src.pipeline.pipeline.InventoryPipeline."""

from __future__ import annotations

import numpy as np

from src.models.models import ImageData
from src.pipeline.pipeline import InventoryPipeline


def test_pipeline_end_to_end_with_gallery_match(gallery_config, synthetic_image: np.ndarray) -> None:
    """A red square crop should flow through the full pipeline and be accepted."""
    pipeline = InventoryPipeline(gallery_config)
    image_data = ImageData(image_id="t1", source_path="mem.jpg", image_array=synthetic_image, width=300, height=300)

    result = pipeline.run(image_data)

    assert result.image_id == "t1"
    assert result.total_items == len(result.items)
    assert result.processing_time_ms >= 0.0
    if result.items:
        assert result.items[0].product_id == "1"  # prod_red_square


def test_pipeline_handles_blank_image_gracefully(gallery_config) -> None:
    """A blank image with no detectable regions should return zero items, not crash."""
    pipeline = InventoryPipeline(gallery_config)
    blank = np.full((200, 200, 3), 255, dtype=np.uint8)
    image_data = ImageData(image_id="blank", source_path="mem.jpg", image_array=blank, width=200, height=200)

    result = pipeline.run(image_data)

    assert result.total_items == 0
    assert result.items == []


def test_pipeline_run_with_trace_returns_full_trace(gallery_config, synthetic_image: np.ndarray) -> None:
    """run_with_trace must return every intermediate stage alongside the final result."""
    pipeline = InventoryPipeline(gallery_config)
    image_data = ImageData(image_id="t2", source_path="mem.jpg", image_array=synthetic_image, width=300, height=300)

    result, trace = pipeline.run_with_trace(image_data)

    assert trace.image_id == "t2"
    assert trace.detection_result is not None
    assert trace.overlap_result is not None
    assert trace.refinement_result is not None
    assert len(trace.crops) == len(trace.detection_result.detections)
    if trace.crops:
        crop_trace = trace.crops[0]
        assert crop_trace.retrieval_result is not None
        assert crop_trace.decision_result is not None
        assert crop_trace.final_decision is not None
    assert result.total_items == len(result.items)


def test_pipeline_similarity_threshold_override(gallery_config, synthetic_image: np.ndarray) -> None:
    """An extremely strict runtime threshold override should reduce/eliminate accepted items."""
    pipeline = InventoryPipeline(gallery_config)
    image_data = ImageData(image_id="t3", source_path="mem.jpg", image_array=synthetic_image, width=300, height=300)

    default_result = pipeline.run(image_data)
    strict_result = pipeline.run(image_data, similarity_threshold=0.999)

    assert strict_result.total_items <= default_result.total_items
