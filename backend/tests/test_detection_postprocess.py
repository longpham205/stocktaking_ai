"""Unit tests for engine.detection.postprocess.suppress_redundant."""

from __future__ import annotations

from engine.detection.postprocess import REASON_CONTAINER, REASON_DUPLICATE, drop_nested_same_product, suppress_redundant
from engine.models.models import BoundingBox, Detection


def _det(x1: float, y1: float, x2: float, y2: float, confidence: float) -> Detection:
    return Detection(bbox=BoundingBox(x1, y1, x2, y2), confidence=confidence)


def test_duplicate_keeps_the_more_confident_box() -> None:
    """Two boxes on the same object collapse into the more confident one."""
    strong = _det(0, 0, 100, 100, 0.9)
    weak = _det(5, 5, 105, 105, 0.6)

    kept, dropped = suppress_redundant([weak, strong], duplicate_iou=0.6, container_min_boxes=0, containment_ratio=0.8)

    assert kept == [strong]
    assert dropped == [(weak, REASON_DUPLICATE)]


def test_neighbours_and_partial_overlaps_are_kept() -> None:
    """Products side by side, or one lying across another, are different objects."""
    left = _det(0, 0, 100, 100, 0.9)
    right = _det(100, 0, 200, 100, 0.8)
    across = _det(50, 40, 150, 140, 0.7)

    kept, dropped = suppress_redundant([left, right, across], duplicate_iou=0.6, container_min_boxes=0, containment_ratio=0.8)

    assert kept == [left, right, across]
    assert dropped == []


def test_small_box_inside_a_large_one_is_kept() -> None:
    """A small product lying on a large one is not a duplicate of it."""
    large = _det(0, 0, 400, 400, 0.9)
    small = _det(50, 50, 150, 150, 0.8)

    kept, _ = suppress_redundant([large, small], duplicate_iou=0.6, container_min_boxes=0, containment_ratio=0.8)

    assert kept == [large, small]


def test_container_rule_is_off_at_zero_and_drops_group_boxes_when_on() -> None:
    """A box around two products is kept with the rule off and dropped with it on."""
    group = _det(0, 0, 400, 200, 0.6)
    first = _det(10, 10, 150, 190, 0.9)
    second = _det(210, 10, 390, 190, 0.8)

    kept_off, _ = suppress_redundant([group, first, second], duplicate_iou=0.6, container_min_boxes=0, containment_ratio=0.8)
    kept_on, dropped_on = suppress_redundant([group, first, second], duplicate_iou=0.6, container_min_boxes=2, containment_ratio=0.8)

    assert kept_off == [first, second, group]
    assert kept_on == [first, second]
    assert dropped_on == [(group, REASON_CONTAINER)]


def test_three_copies_of_one_box_leave_one_even_with_the_container_rule() -> None:
    """Duplicates are removed first, so copies never count as each other's contents."""
    copies = [_det(0, 0, 300, 300, 0.7), _det(2, 2, 302, 302, 0.65), _det(4, 0, 300, 304, 0.6)]

    kept, dropped = suppress_redundant(copies, duplicate_iou=0.6, container_min_boxes=2, containment_ratio=0.8)

    assert kept == [copies[0]]
    assert [reason for _, reason in dropped] == [REASON_DUPLICATE, REASON_DUPLICATE]


def _nested(items, ratio):
    return drop_nested_same_product(items, lambda item: item[0], lambda item: item[1], ratio)


def test_nested_same_product_drops_the_part_inside_the_whole() -> None:
    """A tube boxed on its own inside the box of its backing card is the same product."""
    card = (BoundingBox(0, 0, 200, 400), "33")
    tube = (BoundingBox(40, 50, 160, 380), "33")
    other = (BoundingBox(300, 0, 400, 100), "33")

    assert _nested([tube, card, other], 0.8) == ([card, other], [tube])


def test_nested_rule_keeps_other_products_and_is_off_at_zero() -> None:
    """A different product lying on top, or the rule switched off, keeps every item."""
    card = (BoundingBox(0, 0, 200, 400), "33")
    lipstick = (BoundingBox(40, 50, 160, 380), "7")
    tube = (BoundingBox(40, 50, 160, 380), "33")

    assert _nested([card, lipstick], 0.8) == ([card, lipstick], [])
    assert _nested([card, tube], 0.0) == ([card, tube], [])
