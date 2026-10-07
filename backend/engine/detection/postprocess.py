"""Removal of redundant detector boxes.

RF-DETR has no suppression step of its own, so one product can come back as several boxes and
every box becomes a line on the invoice. Two geometric rules:

    1. Duplicates: two boxes with IoU >= `duplicate_iou` cover the same object; the one with the
       lower confidence is dropped. On the validation set (2026-10-06) this removed 4 boxes and
       no product lost its box.
    2. Containers: a box that encloses `container_min_boxes` or more other boxes (each with at
       least `containment_ratio` of its area inside) is usually a box around a group of products
       (a plastic bag, a pile). OFF by default: on the same set it removed 2 group boxes but also
       the box of a real product that had two other products lying on top of it.

A third rule runs after recognition, because it needs the SKU: two boxes of the SAME product where
one lies (almost) entirely inside the other are one object counted twice, e.g. the tube of a cream
sold on a backing card is boxed on its own as well as together with the card, or a plastic bag is
boxed around a product and recognised as that product. The LESS CONFIDENT box of the pair is
dropped: always dropping the inner box removed real products sitting inside a low-confidence bag
box (2026-10-07). OFF by default.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from engine.models.models import BoundingBox, Detection

T = TypeVar("T")

REASON_DUPLICATE = "duplicate"
REASON_CONTAINER = "container"


def _inside_ratio(inner: BoundingBox, outer: BoundingBox) -> float:
    """Share of `inner`'s area that lies within `outer`."""
    area = inner.area
    return inner.intersection_area(outer) / area if area > 0 else 0.0


def suppress_redundant(
    detections: list[Detection],
    duplicate_iou: float,
    container_min_boxes: int,
    containment_ratio: float,
) -> tuple[list[Detection], list[tuple[Detection, str]]]:
    """Drop duplicate boxes, then boxes that enclose a group of other boxes.

    Duplicates go first: several near-identical boxes would otherwise count as each other's
    contents and the container rule would remove all of them, losing the product.

    Args:
        detections: Detector output for one image.
        duplicate_iou: IoU at or above which two boxes are the same object.
        container_min_boxes: Number of enclosed boxes that makes a box a container; 0 turns the
            container rule off.
        containment_ratio: Share of a box's area that must lie inside another box for it to count
            as enclosed.

    Returns:
        (kept, dropped): kept detections in descending confidence, and each dropped detection
        with the reason it was dropped.
    """
    ordered = sorted(detections, key=lambda item: item.confidence, reverse=True)
    dropped: list[tuple[Detection, str]] = []

    unique: list[Detection] = []
    for candidate in ordered:
        if any(candidate.bbox.iou(kept.bbox) >= duplicate_iou for kept in unique):
            dropped.append((candidate, REASON_DUPLICATE))
        else:
            unique.append(candidate)

    if container_min_boxes <= 0:
        return unique, dropped

    kept: list[Detection] = []
    for candidate in unique:
        enclosed = sum(
            1
            for other in unique
            if other is not candidate and _inside_ratio(other.bbox, candidate.bbox) >= containment_ratio
        )
        if enclosed >= container_min_boxes:
            dropped.append((candidate, REASON_CONTAINER))
        else:
            kept.append(candidate)
    return kept, dropped


def _nested(a: BoundingBox, b: BoundingBox, ratio: float) -> bool:
    """True when the smaller of the two boxes has at least `ratio` of its area inside the other."""
    inner, outer = (a, b) if a.area <= b.area else (b, a)
    return _inside_ratio(inner, outer) >= ratio


def drop_nested_same_product(
    items: list[T],
    bbox_of: Callable[[T], BoundingBox],
    product_of: Callable[[T], str],
    confidence_of: Callable[[T], float],
    ratio: float,
) -> tuple[list[T], list[T]]:
    """Of two nested boxes recognised as the same product, drop the less confident one.

    Args:
        items: Recognised items of one image.
        bbox_of: Returns an item's box.
        product_of: Returns an item's product_id.
        confidence_of: Returns an item's detection confidence.
        ratio: Share of the smaller box's area that must lie inside the larger one; 0 turns the
            rule off.

    Returns:
        (kept, dropped), both in the input order.
    """
    if ratio <= 0:
        return list(items), []
    kept_idx: list[int] = []
    dropped_idx: set[int] = set()
    for i in sorted(range(len(items)), key=lambda i: confidence_of(items[i]), reverse=True):
        if any(
            product_of(items[j]) == product_of(items[i]) and _nested(bbox_of(items[i]), bbox_of(items[j]), ratio)
            for j in kept_idx
        ):
            dropped_idx.add(i)
        else:
            kept_idx.append(i)
    return [it for i, it in enumerate(items) if i not in dropped_idx], [it for i, it in enumerate(items) if i in dropped_idx]
