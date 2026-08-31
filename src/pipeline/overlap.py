"""Overlap analysis for detected product regions.

Responsibility: determine which detections have suspicious spatial
overlap and therefore warrant segmentation refinement. This module is
purely geometric — it never removes, suppresses, or mutates any
``Detection``, never performs detection/segmentation/retrieval/product
identification itself, and never decides which segmentation backend to
use (that decision belongs to ``Refiner``).

This is intentionally different from classical NMS: NMS *removes*
duplicate boxes. ``OverlapResolver`` only *flags* suspicious groups for
the (optional) segmentation stage to resolve; the original detections
are always preserved unchanged, per 02_MODULE_SPECIFICATION.md Section
3.1 ("Detector output must not be mutated by downstream stages").
"""

from __future__ import annotations

from src.core.config import AppConfig
from src.core.logger import get_logger
from src.core.utils import timer
from src.models.models import (
    Detection,
    DetectionResult,
    OverlapGroup,
    OverlapPair,
    OverlapResult,
)

logger = get_logger(__name__)


class OverlapResolver:
    """Flags suspiciously overlapping detections for segmentation refinement."""

    def __init__(self, config: AppConfig) -> None:
        """Initializes the OverlapResolver with its trigger policy.

        Args:
            config: Fully validated application configuration.
        """
        self._trigger = config.refinement.trigger
        logger.info(
            "OverlapResolver initialized (enabled=%s iou_threshold=%.3f "
            "overlap_ratio_threshold=%.3f min_overlapping_pairs=%d "
            "require_multiple_detections=%s)",
            self._trigger.enabled,
            self._trigger.iou_threshold,
            self._trigger.overlap_ratio_threshold,
            self._trigger.min_overlapping_pairs,
            self._trigger.require_multiple_detections,
        )

    def resolve(self, detection_result: DetectionResult) -> OverlapResult:
        """Analyzes pairwise geometric overlap among detections.

        Args:
            detection_result: Detections produced by the Detector.

        Returns:
            An OverlapResult describing every suspicious pair and the
            connected groups they form. ``needs_refinement`` is True only
            when the configured trigger policy is satisfied.
        """
        with timer() as elapsed:
            detections = detection_result.detections

            if not self._trigger.enabled:
                return OverlapResult(
                    image_id=detection_result.image_id,
                    processing_time_ms=elapsed["elapsed_ms"],
                )

            if self._trigger.require_multiple_detections and len(detections) < 2:
                return OverlapResult(
                    image_id=detection_result.image_id,
                    processing_time_ms=elapsed["elapsed_ms"],
                )

            pairs = find_suspicious_pairs(
                detections, self._trigger.iou_threshold, self._trigger.overlap_ratio_threshold
            )
            needs_refinement = len(pairs) >= self._trigger.min_overlapping_pairs

            groups = self._group_pairs(pairs) if needs_refinement else []

        result = OverlapResult(
            image_id=detection_result.image_id,
            pairs=pairs,
            groups=groups,
            needs_refinement=needs_refinement,
            processing_time_ms=elapsed["elapsed_ms"],
        )

        if needs_refinement:
            logger.info(
                "OverlapResolver flagged %d suspicious pair(s) forming %d group(s) "
                "for image_id='%s' (%.2f ms)",
                len(pairs),
                len(groups),
                detection_result.image_id,
                elapsed["elapsed_ms"],
            )

        return result

    @staticmethod
    def _group_pairs(pairs: list[OverlapPair]) -> list[OverlapGroup]:
        """Groups suspicious pairs into connected components (union-find).

        Example: if detection 0 overlaps 1, and 1 overlaps 2, all three
        detections belong to the same OverlapGroup even though 0 and 2
        may not directly overlap.

        Args:
            pairs: Suspicious pairs produced by `_find_suspicious_pairs`.

        Returns:
            List of OverlapGroup objects, each with a stable `group_id`
            starting at 0.
        """
        parent: dict[int, int] = {}

        def find(node: int) -> int:
            while parent.setdefault(node, node) != node:
                parent[node] = parent[parent[node]]
                node = parent[node]
            return node

        def union(node_a: int, node_b: int) -> None:
            root_a, root_b = find(node_a), find(node_b)
            if root_a != root_b:
                parent[root_a] = root_b

        for pair in pairs:
            union(pair.detection_index_a, pair.detection_index_b)

        members: dict[int, set[int]] = {}
        pairs_by_root: dict[int, list[OverlapPair]] = {}

        for pair in pairs:
            root = find(pair.detection_index_a)
            members.setdefault(root, set()).update(
                (pair.detection_index_a, pair.detection_index_b)
            )
            pairs_by_root.setdefault(root, []).append(pair)

        groups: list[OverlapGroup] = []
        for group_id, root in enumerate(sorted(members.keys())):
            groups.append(
                OverlapGroup(
                    group_id=group_id,
                    detection_indices=sorted(members[root]),
                    pairs=pairs_by_root[root],
                )
            )
        return groups


def find_suspicious_pairs(
    detections: list[Detection],
    iou_threshold: float,
    overlap_ratio_threshold: float,
) -> list[OverlapPair]:
    """Computes geometric relationships for every detection pair.

    Pure function (no config/state) exposed as the single source of
    truth for "which pairs count as suspicious overlap" — used by
    `OverlapResolver.resolve` at runtime AND by VAL's Overlap-stage
    evaluator (Stage 3), so the two never compute a different answer to
    the same question (VAL spec, Section 15 - Core Rule).

    A pair is "suspicious" when its IoU meets `iou_threshold` OR either
    directional intersection ratio meets `overlap_ratio_threshold`
    (catches cases where a small box is mostly contained inside a larger
    one, which can have low IoU but still indicates overlapping/nested
    products).

    Args:
        detections: All detections for one image.
        iou_threshold: Minimum IoU to flag a pair as suspicious.
        overlap_ratio_threshold: Minimum directional intersection ratio
            (either direction) to flag a pair as suspicious.

    Returns:
        List of OverlapPair objects for suspicious pairs only.
    """
    pairs: list[OverlapPair] = []

    for index_a in range(len(detections)):
        for index_b in range(index_a + 1, len(detections)):
            box_a = detections[index_a].bbox
            box_b = detections[index_b].bbox

            iou = box_a.iou(box_b)
            ratio_a = box_a.intersection_ratio(box_b)
            ratio_b = box_b.intersection_ratio(box_a)

            is_suspicious = iou >= iou_threshold or max(ratio_a, ratio_b) >= overlap_ratio_threshold
            if not is_suspicious:
                continue

            pairs.append(
                OverlapPair(
                    detection_index_a=index_a,
                    detection_index_b=index_b,
                    iou=iou,
                    intersection_ratio_a=ratio_a,
                    intersection_ratio_b=ratio_b,
                    containment_a_in_b=box_b.contains(box_a),
                    containment_b_in_a=box_a.contains(box_b),
                )
            )

    return pairs
