"""Unit tests for src.models.models DTOs."""

from __future__ import annotations

from src.models.models import (
    BoundingBox,
    DetectionResult,
    InventoryResult,
    RefinementResult,
)


def test_bounding_box_area_and_dimensions() -> None:
    """BoundingBox should correctly compute width, height, and area."""
    box = BoundingBox(x1=10, y1=20, x2=50, y2=70)
    assert box.width == 40
    assert box.height == 50
    assert box.area == 2000


def test_bounding_box_iou_full_overlap() -> None:
    """Identical boxes must have an IoU of exactly 1.0."""
    box_a = BoundingBox(0, 0, 10, 10)
    box_b = BoundingBox(0, 0, 10, 10)
    assert box_a.iou(box_b) == 1.0


def test_bounding_box_iou_no_overlap() -> None:
    """Disjoint boxes must have an IoU of exactly 0.0."""
    box_a = BoundingBox(0, 0, 10, 10)
    box_b = BoundingBox(20, 20, 30, 30)
    assert box_a.iou(box_b) == 0.0


def test_bounding_box_intersection_ratio_directional() -> None:
    """intersection_ratio should be directional (self.area denominator)."""
    small = BoundingBox(0, 0, 10, 10)
    large = BoundingBox(0, 0, 100, 100)
    assert small.intersection_ratio(large) == 1.0
    assert large.intersection_ratio(small) == 0.01


def test_bounding_box_contains() -> None:
    """contains() should correctly detect full containment."""
    outer = BoundingBox(0, 0, 100, 100)
    inner = BoundingBox(10, 10, 20, 20)
    assert outer.contains(inner) is True
    assert inner.contains(outer) is False


def test_detection_result_defaults_to_empty_list() -> None:
    """DetectionResult.detections should default to an empty, independent list."""
    result_a = DetectionResult(image_id="a")
    result_b = DetectionResult(image_id="b")
    result_a.detections.append("sentinel")  # type: ignore[arg-type]
    assert result_b.detections == []


def test_inventory_result_total_items_field() -> None:
    """InventoryResult should store total_items independently of items length."""
    result = InventoryResult(image_id="img", source_path="path.jpg", total_items=3)
    assert result.total_items == 3
    assert result.items == []


def test_refinement_result_get_refined_box() -> None:
    """RefinementResult.get_refined_box should find/miss correctly by index."""
    from src.models.models import RefinedBox

    result = RefinementResult(
        image_id="img",
        triggered=True,
        backend="mock_refiner",
        refined_boxes=[
            RefinedBox(
                detection_index=2,
                refined_bbox=BoundingBox(0, 0, 10, 10),
                mask_area_ratio=1.0,
                refinement_confidence=0.9,
                backend="mock_refiner",
            )
        ],
    )
    assert result.get_refined_box(2) is not None
    assert result.get_refined_box(0) is None
