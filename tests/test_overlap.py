"""Unit tests for src.pipeline.overlap.OverlapResolver."""

from __future__ import annotations

from src.models.models import BoundingBox, Detection, DetectionResult
from src.pipeline.overlap import OverlapResolver


def test_overlap_resolver_flags_overlapping_pair(test_config) -> None:
    """Two heavily overlapping boxes must be flagged as needing refinement."""
    resolver = OverlapResolver(test_config)
    detections = [
        Detection(bbox=BoundingBox(0, 0, 100, 100), confidence=0.9),
        Detection(bbox=BoundingBox(50, 50, 150, 150), confidence=0.8),
    ]
    result = resolver.resolve(DetectionResult(image_id="i1", detections=detections))

    assert result.needs_refinement is True
    assert len(result.pairs) == 1
    assert result.groups[0].detection_indices == [0, 1]


def test_overlap_resolver_ignores_disjoint_boxes(test_config) -> None:
    """Well-separated boxes must not trigger refinement."""
    resolver = OverlapResolver(test_config)
    detections = [
        Detection(bbox=BoundingBox(0, 0, 50, 50), confidence=0.9),
        Detection(bbox=BoundingBox(500, 500, 550, 550), confidence=0.8),
    ]
    result = resolver.resolve(DetectionResult(image_id="i2", detections=detections))

    assert result.needs_refinement is False
    assert result.pairs == []
    assert result.groups == []


def test_overlap_resolver_never_mutates_detections(test_config) -> None:
    """OverlapResolver must never remove or alter any Detection (not NMS)."""
    resolver = OverlapResolver(test_config)
    detections = [
        Detection(bbox=BoundingBox(0, 0, 100, 100), confidence=0.9),
        Detection(bbox=BoundingBox(10, 10, 110, 110), confidence=0.1),  # low confidence, heavily overlapping
    ]
    detection_result = DetectionResult(image_id="i3", detections=detections)
    resolver.resolve(detection_result)

    # Both detections must still be present and unchanged.
    assert len(detection_result.detections) == 2
    assert detection_result.detections[1].confidence == 0.1


def test_overlap_resolver_single_detection_no_refinement(test_config) -> None:
    """A single detection can never overlap anything; must skip refinement."""
    resolver = OverlapResolver(test_config)
    detections = [Detection(bbox=BoundingBox(0, 0, 100, 100), confidence=0.9)]
    result = resolver.resolve(DetectionResult(image_id="i4", detections=detections))

    assert result.needs_refinement is False


def test_overlap_resolver_groups_transitive_overlaps(test_config) -> None:
    """Three detections in a chain (0-1, 1-2) must form a single group of 3."""
    resolver = OverlapResolver(test_config)
    detections = [
        Detection(bbox=BoundingBox(0, 0, 100, 100), confidence=0.9),
        Detection(bbox=BoundingBox(90, 0, 190, 100), confidence=0.9),
        Detection(bbox=BoundingBox(180, 0, 280, 100), confidence=0.9),
    ]
    result = resolver.resolve(DetectionResult(image_id="i5", detections=detections))

    assert result.needs_refinement is True
    assert len(result.groups) == 1
    assert result.groups[0].detection_indices == [0, 1, 2]


def test_overlap_resolver_disabled_via_config(test_config) -> None:
    """When trigger.enabled is False, refinement must never be requested."""
    disabled_trigger = test_config.refinement.trigger.model_copy(update={"enabled": False})
    disabled_refinement = test_config.refinement.model_copy(update={"trigger": disabled_trigger})
    config = test_config.model_copy(update={"refinement": disabled_refinement})

    resolver = OverlapResolver(config)
    detections = [
        Detection(bbox=BoundingBox(0, 0, 100, 100), confidence=0.9),
        Detection(bbox=BoundingBox(10, 10, 110, 110), confidence=0.8),
    ]
    result = resolver.resolve(DetectionResult(image_id="i6", detections=detections))

    assert result.needs_refinement is False
