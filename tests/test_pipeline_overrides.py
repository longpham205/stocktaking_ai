"""P1-1 / P1-2 / P1-3: override ngưỡng theo từng lần gọi (kèm cache) và cờ `has_overlap`."""

from __future__ import annotations

import numpy as np

from src.models.models import ImageData, InventoryResult
from src.pipeline.pipeline import InventoryPipeline


def _image(image_id: str, arr: np.ndarray) -> ImageData:
    h, w = arr.shape[:2]
    return ImageData(image_id=image_id, source_path="mem.jpg", image_array=arr, width=w, height=h)


def test_inventory_result_has_overlap_defaults_to_false() -> None:
    assert InventoryResult(image_id="a", source_path="b").has_overlap is False


def test_has_overlap_matches_overlap_stage_output(gallery_config, synthetic_multi_image: np.ndarray) -> None:
    pipeline = InventoryPipeline(gallery_config)
    result = pipeline.run(_image("o1", synthetic_multi_image))
    traced, trace = pipeline.run_with_trace(_image("o2", synthetic_multi_image))

    assert isinstance(result.has_overlap, bool)
    assert result.has_overlap == trace.overlap_result.needs_refinement
    assert traced.has_overlap == trace.overlap_result.needs_refinement


def test_min_confidence_accept_override_is_stricter(gallery_config, synthetic_image: np.ndarray) -> None:
    pipeline = InventoryPipeline(gallery_config)
    baseline = pipeline.run(_image("m1", synthetic_image))
    strict = pipeline.run(_image("m2", synthetic_image), min_confidence_accept=1.0)

    accepted = lambda r: sum(1 for i in r.items if i.status == "accepted")  # noqa: E731
    assert accepted(strict) <= accepted(baseline)


def test_no_override_uses_shared_components(gallery_config) -> None:
    pipeline = InventoryPipeline(gallery_config)
    engine, reranker = pipeline._resolve_decision_components(None, None)
    assert engine is pipeline._decision_engine and reranker is pipeline._reranker


def test_override_pair_is_cached_by_threshold_values(gallery_config) -> None:
    pipeline = InventoryPipeline(gallery_config)
    a = pipeline._resolve_decision_components(0.7, None)
    b = pipeline._resolve_decision_components(0.7, None)
    c = pipeline._resolve_decision_components(0.7, 0.9)

    assert a[0] is b[0] and a[1] is b[1]  # cùng ngưỡng -> không dựng lại
    assert c[0] is not a[0] and c[1] is not a[1]  # ngưỡng khác -> cặp khác
    assert a[0] is not pipeline._decision_engine  # override không đụng cặp dùng chung


def test_override_cache_is_bounded(gallery_config) -> None:
    pipeline = InventoryPipeline(gallery_config)
    for i in range(20):
        pipeline._resolve_decision_components(0.30 + i / 100, None)
    assert len(pipeline._override_cache) <= 8


def test_detected_count_matches_detector_and_covers_items(gallery_config, synthetic_multi_image: np.ndarray) -> None:
    pipeline = InventoryPipeline(gallery_config)
    result = pipeline.run(_image("d1", synthetic_multi_image))
    traced, trace = pipeline.run_with_trace(_image("d2", synthetic_multi_image))

    assert result.detected_count == len(trace.detection_result.detections)
    assert traced.detected_count == len(trace.detection_result.detections)
    assert result.detected_count >= len(result.items)  # item bị 'rejected' bị loại nên không thể nhiều hơn số vật tìm thấy
