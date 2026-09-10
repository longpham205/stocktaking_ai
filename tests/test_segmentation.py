"""Unit tests for src.segmentation (Refiner + backends)."""

from __future__ import annotations

import numpy as np
import pytest

from src.models.models import (
    BoundingBox,
    Detection,
    DetectionResult,
    OverlapGroup,
    OverlapPair,
    OverlapResult,
)
from src.segmentation.backends.mock_refiner import MockRefinerBackend
from src.segmentation.refiner import Refiner


def _overlap_result_for(indices: list[int]) -> OverlapResult:
    """Builds a minimal OverlapResult flagging the given detection indices."""
    pairs = (
        [
            OverlapPair(
                detection_index_a=indices[0],
                detection_index_b=indices[1],
                iou=0.5,
                intersection_ratio_a=0.5,
                intersection_ratio_b=0.5,
            )
        ]
        if len(indices) >= 2
        else []
    )
    return OverlapResult(
        image_id="img",
        pairs=pairs,
        groups=[OverlapGroup(group_id=0, detection_indices=indices, pairs=pairs)],
        needs_refinement=True,
    )


def test_mock_refiner_backend_returns_fallback() -> None:
    """MockRefinerBackend must always return a fallback RefinedBox."""
    backend = MockRefinerBackend()
    bbox = BoundingBox(0, 0, 10, 10)
    result = backend.refine(np.zeros((10, 10, 3), dtype=np.uint8), bbox, detection_index=1)

    assert result.used_fallback is True
    assert result.refined_bbox is bbox
    assert result.detection_index == 1


def test_refiner_backend_none_never_triggers(test_config) -> None:
    """With backend='none', Refiner must always return an untriggered result."""
    updated_refinement = test_config.refinement.model_copy(update={"backend": "none"})
    config = test_config.model_copy(update={"refinement": updated_refinement})
    refiner = Refiner(config)

    detections = [Detection(bbox=BoundingBox(0, 0, 100, 100), confidence=0.9)]
    detection_result = DetectionResult(image_id="img", detections=detections)
    overlap_result = _overlap_result_for([0])
    image = np.zeros((200, 200, 3), dtype=np.uint8)

    result = refiner.refine(image, detection_result, overlap_result)

    assert result.triggered is False
    assert result.refined_boxes == []


def test_refiner_backend_mock_refiner_produces_fallback_boxes(test_config) -> None:
    """With backend='mock_refiner', Refiner runs but every box is a fallback."""
    updated_refinement = test_config.refinement.model_copy(update={"backend": "mock_refiner"})
    config = test_config.model_copy(update={"refinement": updated_refinement})
    refiner = Refiner(config)

    detections = [
        Detection(bbox=BoundingBox(0, 0, 100, 100), confidence=0.9),
        Detection(bbox=BoundingBox(50, 50, 150, 150), confidence=0.8),
    ]
    detection_result = DetectionResult(image_id="img", detections=detections)
    overlap_result = _overlap_result_for([0, 1])
    image = np.zeros((200, 200, 3), dtype=np.uint8)

    result = refiner.refine(image, detection_result, overlap_result)

    assert result.triggered is True
    assert result.backend == "mock_refiner"
    assert len(result.refined_boxes) == 2
    assert all(box.used_fallback for box in result.refined_boxes)


def test_refiner_skips_when_overlap_not_needed(test_config) -> None:
    """Refiner must not run the backend at all when overlap_result.needs_refinement is False."""
    updated_refinement = test_config.refinement.model_copy(update={"backend": "mock_refiner"})
    config = test_config.model_copy(update={"refinement": updated_refinement})
    refiner = Refiner(config)

    detections = [Detection(bbox=BoundingBox(0, 0, 100, 100), confidence=0.9)]
    detection_result = DetectionResult(image_id="img", detections=detections)
    overlap_result = OverlapResult(image_id="img", needs_refinement=False)
    image = np.zeros((200, 200, 3), dtype=np.uint8)

    result = refiner.refine(image, detection_result, overlap_result)

    assert result.triggered is False
    assert result.refined_boxes == []


def test_refiner_rejects_unknown_backend(test_config) -> None:
    """Refiner must raise ValueError for a backend with no registered loader."""
    updated_refinement = test_config.refinement.model_copy(update={"backend": "not_a_real_backend"})
    config = test_config.model_copy(update={"refinement": updated_refinement})

    with pytest.raises(ValueError, match="Unsupported refinement backend"):
        Refiner(config)
