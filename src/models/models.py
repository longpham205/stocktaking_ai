"""Standardized Data Transfer Objects (DTOs) shared across all modules.

Every pipeline stage communicates exclusively through these dataclasses.
No un-typed dictionaries are permitted between core modules (see
03_DEVELOPMENT_RULES.md, Rule 21). This module contains no business logic
and imports no AI/ML libraries (see 02_MODULE_SPECIFICATION.md, Section 2).

The overlap/segmentation DTOs in this module are model-agnostic. They
describe geometric relationships between detections and do not assume any
specific refinement or segmentation backend such as SAM2.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


# =============================================================================
# Bounding Box
# =============================================================================


@dataclass
class BoundingBox:
    """Axis-aligned bounding box in absolute pixel coordinates.

    Attributes:
        x1: Left edge x-coordinate.
        y1: Top edge y-coordinate.
        x2: Right edge x-coordinate.
        y2: Bottom edge y-coordinate.
    """

    x1: float
    y1: float
    x2: float
    y2: float

    @property
    def width(self) -> float:
        """Returns the width of the bounding box."""
        return max(0.0, self.x2 - self.x1)

    @property
    def height(self) -> float:
        """Returns the height of the bounding box."""
        return max(0.0, self.y2 - self.y1)

    @property
    def area(self) -> float:
        """Returns the area of the bounding box."""
        return self.width * self.height

    def as_list(self) -> list[float]:
        """Returns the bounding box as [x1, y1, x2, y2]."""
        return [self.x1, self.y1, self.x2, self.y2]

    def iou(self, other: "BoundingBox") -> float:
        """Computes Intersection-over-Union against another bounding box.

        Args:
            other: The bounding box to compare against.

        Returns:
            IoU score in the range [0.0, 1.0].
        """
        intersection = self.intersection_area(other)
        union = self.area + other.area - intersection
        if union <= 0.0:
            return 0.0
        return intersection / union

    def intersection_area(self, other: "BoundingBox") -> float:
        """Returns the intersection area with another bounding box.

        Args:
            other: The bounding box to compare against.

        Returns:
            Intersection area in pixels squared.
        """
        inter_x1 = max(self.x1, other.x1)
        inter_y1 = max(self.y1, other.y1)
        inter_x2 = min(self.x2, other.x2)
        inter_y2 = min(self.y2, other.y2)

        inter_width = max(0.0, inter_x2 - inter_x1)
        inter_height = max(0.0, inter_y2 - inter_y1)

        return inter_width * inter_height

    def intersection_ratio(self, other: "BoundingBox") -> float:
        """Returns intersection area relative to this bounding box's area.

        This is directional: ``self.intersection_ratio(other)`` means
        ``intersection_area(self, other) / self.area``. Useful for
        detecting cases where one box is mostly contained inside another
        even when IoU itself is relatively low.

        Args:
            other: The bounding box to compare against.

        Returns:
            Ratio in the range [0.0, 1.0].
        """
        if self.area <= 0.0:
            return 0.0
        return self.intersection_area(other) / self.area

    def contains(self, other: "BoundingBox") -> bool:
        """Returns whether this bounding box fully contains another box.

        Args:
            other: The bounding box to test.

        Returns:
            True if ``other`` is fully contained by ``self``.
        """
        return (
            self.x1 <= other.x1
            and self.y1 <= other.y1
            and self.x2 >= other.x2
            and self.y2 >= other.y2
        )


# =============================================================================
# Image
# =============================================================================


@dataclass
class ImageData:
    """Container for a single loaded image and its metadata.

    Attributes:
        image_id: Unique identifier for this image.
        source_path: Filesystem path the image was loaded from.
        image_array: Raw image pixel array (BGR, HxWx3).
        width: Image width in pixels.
        height: Image height in pixels.
    """

    image_id: str
    source_path: str
    image_array: np.ndarray
    width: int
    height: int


# =============================================================================
# Detection
# =============================================================================


@dataclass
class Detection:
    """A single detected object region.

    Attributes:
        bbox: Bounding box of the detected region.
        confidence: Detector confidence score in [0.0, 1.0].
        class_id: Integer class identifier assigned by the detector.
        class_name: Human-readable class label assigned by the detector.
    """

    bbox: BoundingBox
    confidence: float
    class_id: int = 0
    class_name: str = "product"


@dataclass
class DetectionResult:
    """Aggregated output of the Detector module for one image.

    Attributes:
        image_id: Identifier of the source image.
        detections: List of detected regions.
        processing_time_ms: Wall-clock time spent on detection, in ms.
    """

    image_id: str
    detections: list[Detection] = field(default_factory=list)
    processing_time_ms: float = 0.0


# =============================================================================
# Overlap Analysis
# =============================================================================


@dataclass
class OverlapPair:
    """Geometric relationship between two detected regions.

    This DTO is model-agnostic. It does not represent a segmentation result
    and does not depend on SAM2 or any other segmentation backend.

    Detection indices refer to positions inside the corresponding
    ``DetectionResult.detections`` list.

    Attributes:
        detection_index_a: Index of the first detection.
        detection_index_b: Index of the second detection.
        iou: Intersection-over-Union between the two boxes.
        intersection_ratio_a: Intersection area divided by area of A.
        intersection_ratio_b: Intersection area divided by area of B.
        containment_a_in_b: Whether A is fully contained by B.
        containment_b_in_a: Whether B is fully contained by A.
    """

    detection_index_a: int
    detection_index_b: int
    iou: float
    intersection_ratio_a: float
    intersection_ratio_b: float
    containment_a_in_b: bool = False
    containment_b_in_a: bool = False

    @property
    def max_intersection_ratio(self) -> float:
        """Returns the larger directional intersection ratio."""
        return max(self.intersection_ratio_a, self.intersection_ratio_b)

    @property
    def is_containment(self) -> bool:
        """Returns whether either detection fully contains the other."""
        return self.containment_a_in_b or self.containment_b_in_a


@dataclass
class OverlapGroup:
    """A connected group of detections with suspicious spatial overlap.

    A group may contain more than two detections. For example, if
    detection 0 overlaps detection 1 and detection 1 overlaps detection 2,
    all three detections belong to the same group.

    Attributes:
        group_id: Stable index of this group within an OverlapResult.
        detection_indices: Indices of detections belonging to this group.
        pairs: Pairwise overlap relationships forming this group.
    """

    group_id: int
    detection_indices: list[int] = field(default_factory=list)
    pairs: list[OverlapPair] = field(default_factory=list)

    @property
    def size(self) -> int:
        """Returns the number of detections in this group."""
        return len(self.detection_indices)


@dataclass
class OverlapResult:
    """Aggregated geometric overlap analysis for one detection result.

    This is the contract between ``OverlapResolver`` and the optional
    segmentation/refinement stage. It only answers "which detections
    appear to overlap?" — it never decides which segmentation model to
    use, and it never removes/mutates any Detection.

    Attributes:
        image_id: Identifier of the source image.
        pairs: All detection pairs considered suspicious.
        groups: Connected groups of suspiciously overlapping detections.
        needs_refinement: True when at least one overlap group exists.
        processing_time_ms: Wall-clock time spent on overlap analysis.
    """

    image_id: str
    pairs: list[OverlapPair] = field(default_factory=list)
    groups: list[OverlapGroup] = field(default_factory=list)
    needs_refinement: bool = False
    processing_time_ms: float = 0.0

    @property
    def group_count(self) -> int:
        """Returns the number of overlap groups."""
        return len(self.groups)

    @property
    def overlapping_detection_count(self) -> int:
        """Returns the number of unique detections involved in overlap."""
        indices: set[int] = set()
        for group in self.groups:
            indices.update(group.detection_indices)
        return len(indices)


# =============================================================================
# Segmentation Refinement
# =============================================================================


@dataclass
class RefinedBox:
    """A single refined bounding box produced by a segmentation backend.

    Independent from ``Detection``/``DetectionResult`` by design: the
    original detector bbox is never overwritten. ``Cropper`` decides
    whether to use ``refined_bbox`` or the original ``Detection.bbox``,
    based on ``cropping.use_refined_bbox``.

    Attributes:
        detection_index: Index into the originating
            ``DetectionResult.detections`` list this refinement belongs to.
        refined_bbox: Tighter/adjusted bounding box derived from the mask.
        mask_area_ratio: Mask area divided by the original detection box
            area (coverage ratio); used to validate refinement quality.
        refinement_confidence: Backend-reported mask confidence, in
            [0.0, 1.0] (e.g. SAM2's predicted IoU score).
        backend: Name of the segmentation backend that produced this box
            (e.g. "sam2", "mock_refiner").
        used_fallback: True when the segmentation result was rejected
            (invalid/empty mask, coverage/expansion out of configured
            bounds) and ``refined_bbox`` was set equal to the original
            detection box.
    """

    detection_index: int
    refined_bbox: BoundingBox
    mask_area_ratio: float
    refinement_confidence: float
    backend: str
    used_fallback: bool = False


@dataclass
class RefinementResult:
    """Aggregated output of the segmentation/refinement stage for one image.

    Contract between ``Refiner`` and ``Cropper``. Always produced, even
    when refinement was not triggered or is disabled, so downstream code
    treats "no refinement" uniformly (``refined_boxes`` empty).

    Attributes:
        image_id: Identifier of the source image.
        triggered: Whether ``OverlapResolver`` requested refinement for
            this image.
        backend: Name of the segmentation backend used ("none" when
            refinement was not triggered or is disabled).
        refined_boxes: One ``RefinedBox`` per refined detection.
            Detections absent here keep their original bbox.
        processing_time_ms: Wall-clock time spent on refinement, in ms.
    """

    image_id: str
    triggered: bool = False
    backend: str = "none"
    refined_boxes: list[RefinedBox] = field(default_factory=list)
    processing_time_ms: float = 0.0

    def get_refined_box(self, detection_index: int) -> RefinedBox | None:
        """Looks up the RefinedBox for a given detection index, if any.

        Args:
            detection_index: Index into the originating DetectionResult.

        Returns:
            The matching RefinedBox, or None if that detection was not
            refined.
        """
        for refined_box in self.refined_boxes:
            if refined_box.detection_index == detection_index:
                return refined_box
        return None


# =============================================================================
# Crop
# =============================================================================


@dataclass
class CropImage:
    """A single cropped product region produced by the Cropper module.

    Attributes:
        crop_id: Unique identifier for this crop.
        image_id: Identifier of the parent source image.
        image_array: Cropped pixel array (BGR), resized to
            `cropping.target_size` — used by Retriever (embedding
            backends expect a normalized input size).
        raw_image_array: Cropped pixel array (BGR) at ORIGINAL
            resolution — only boundary-clipped + padded, never resized.
            Used by OCR/Color/Barcode plugins, which lose accuracy when
            fed a downsized image (e.g. small packaging text becomes
            unreadable after a forced resize to 224x224).
        source_bbox: Bounding box actually used to crop (refined bbox when
            available and enabled, otherwise the original detection bbox).
        detection_confidence: Confidence score inherited from Detector.
        detection_index: Index into the originating DetectionResult this
            crop was produced from (traceability for VAL / debugging).
        used_refined_bbox: Whether ``source_bbox`` came from segmentation
            refinement rather than the raw Detector output.
    """

    crop_id: str
    image_id: str
    image_array: np.ndarray
    raw_image_array: np.ndarray
    source_bbox: BoundingBox
    detection_confidence: float
    detection_index: int = -1
    used_refined_bbox: bool = False


# =============================================================================
# Retrieval
# =============================================================================


@dataclass
class RetrievalCandidate:
    """A single ranked candidate returned by the Retriever module.

    Attributes:
        product_id: Unique identifier of the candidate product.
        product_name: Human-readable name of the candidate product.
        similarity_score: Visual similarity score in [0.0, 1.0].
        rank: 1-indexed rank position among returned candidates.
    """

    product_id: str
    product_name: str
    similarity_score: float
    rank: int


@dataclass
class RetrievalResult:
    """Aggregated output of the Retriever module for one crop.

    Attributes:
        crop_id: Identifier of the source crop.
        candidates: Ranked list of retrieval candidates (Top-K).
        processing_time_ms: Wall-clock time spent on retrieval, in ms.
        detection_confidence: Confidence inherited from Detector through
            CropImage — a passthrough field so DecisionEngine.decide()
            keeps a single-argument public API.
    """

    crop_id: str
    candidates: list[RetrievalCandidate] = field(default_factory=list)
    processing_time_ms: float = 0.0
    detection_confidence: float = 0.0

    @property
    def top_candidate(self) -> RetrievalCandidate | None:
        """Returns the highest-ranked candidate, or None if empty."""
        if not self.candidates:
            return None
        return self.candidates[0]


# =============================================================================
# Decision
# =============================================================================


@dataclass
class DecisionResult:
    """Final resolution produced by the Decision Engine for one crop.

    Attributes:
        crop_id: Identifier of the source crop.
        product_id: Resolved product identifier, or None if rejected.
        product_name: Resolved product name, or None if rejected.
        detection_confidence: Confidence inherited from Detector.
        similarity_score: Top-1 similarity score from Retriever.
        final_confidence: Consolidated confidence score.
        status: One of "accepted", "uncertain", or "rejected".
        needs_plugin: Whether secondary plugin evidence was requested.
        trigger_reasons: Subset of {"uncertain", "ambiguous", "force"}
            explaining why ``needs_plugin`` is True. Empty when False.
        forced_plugins: Plugin names that MUST run regardless of
            uncertain/ambiguous status, resolved from
            ``plugins.force_rules`` against every candidate in the
            Retriever's Top-K (not just the winning candidate).
        reason: Human-readable explanation of the decision.
        processing_time_ms: Wall-clock time spent in DecisionEngine.decide,
            in ms.
        rerank_latency_ms: Wall-clock time spent in Reranker.rerank, in
            ms. 0.0 when the decision was never reranked (plugins did
            not run).
    """

    crop_id: str
    product_id: str | None
    product_name: str | None
    detection_confidence: float
    similarity_score: float
    final_confidence: float
    status: str
    needs_plugin: bool
    trigger_reasons: frozenset[str] = field(default_factory=frozenset)
    forced_plugins: frozenset[str] = field(default_factory=frozenset)
    reason: str = ""
    processing_time_ms: float = 0.0
    rerank_latency_ms: float = 0.0


# =============================================================================
# Plugins
# =============================================================================


@dataclass
class PluginResult:
    """Aggregated secondary evidence produced by the Plugin Manager.

    Attributes:
        crop_id: Identifier of the source crop.
        executed_plugins: Names of plugins that actually executed.
        evidence: Mapping of plugin name to raw extracted evidence.
        confidence_boost: Total confidence adjustment contributed by
            plugins (legacy aggregate; Reranker uses ``evidence`` directly
            for per-candidate re-scoring).
        processing_time_ms: Wall-clock time spent running plugins, in ms.
    """

    crop_id: str
    executed_plugins: list[str] = field(default_factory=list)
    evidence: dict[str, dict] = field(default_factory=dict)
    confidence_boost: float = 0.0
    processing_time_ms: float = 0.0


# =============================================================================
# Inventory Item
# =============================================================================


@dataclass
class InventoryItem:
    """A single finalized item within an InventoryResult.

    Attributes:
        product_id: Unique identifier of the identified product.
        product_name: Human-readable name of the identified product.
        bbox: Bounding box coordinates.
        detection_confidence: Confidence score from Object Detector.
        similarity_score: Visual similarity score from Retriever.
        final_confidence: Final consolidated confidence score.
        status: Decision status ("accepted", "uncertain", "rejected").
        plugin_evidence: Optional auxiliary evidence gathered by plugins.
    """

    product_id: str
    product_name: str
    bbox: BoundingBox
    detection_confidence: float
    similarity_score: float
    final_confidence: float
    status: str
    plugin_evidence: dict[str, dict] = field(default_factory=dict)


# =============================================================================
# Final Inventory Result
# =============================================================================


@dataclass
class InventoryResult:
    """The final, structured contract produced by InventoryPipeline.run().

    Attributes:
        image_id: Identifier of the processed source image.
        source_path: Filesystem path of the processed source image.
        items: List of finalized inventory items.
        total_items: Total count of accepted inventory items.
        processing_time_ms: Total end-to-end pipeline latency, in ms.
        timestamp: ISO-8601 timestamp of when processing completed.
    """

    image_id: str
    source_path: str
    items: list[InventoryItem] = field(default_factory=list)
    total_items: int = 0
    processing_time_ms: float = 0.0
    timestamp: str = ""


# =============================================================================
# Pipeline Trace (diagnostics / VAL only — NOT used by default run())
# =============================================================================


@dataclass
class CropTrace:
    """Per-crop intermediate results captured during a traced pipeline run.

    Attributes:
        crop: CropImage produced by Cropper for this detection.
        retrieval_result: Retriever output for this crop.
        decision_result: DecisionEngine output (pre-rerank — i.e. right
            after ``decide()``, before Reranker) for this crop.
        plugin_result: PluginManager output, or None if plugins did not run.
        final_decision: Final DecisionResult after Reranker (identical to
            ``decision_result`` when plugins did not run).
    """

    crop: CropImage
    retrieval_result: RetrievalResult
    decision_result: DecisionResult
    plugin_result: PluginResult | None
    final_decision: DecisionResult


@dataclass
class PipelineTrace:
    """Full intermediate-stage record of a single InventoryPipeline run.

    Produced only by ``InventoryPipeline.run_with_trace(...)``; the
    default ``run(...)`` does not build this, to avoid overhead on the
    hot inference path. VAL uses this trace to evaluate every stage from
    a single real pipeline execution (VAL spec, Section 15 - Core Rule:
    "VAL must measure the actual production pipeline").

    Attributes:
        image_id: Identifier of the source image.
        detection_result: Raw Detector output.
        overlap_result: OverlapResolver output.
        refinement_result: Refiner output (may be untriggered/empty).
        crops: Per-crop trace entries, in Cropper's output order.
    """

    image_id: str
    detection_result: DetectionResult
    overlap_result: OverlapResult
    refinement_result: RefinementResult
    crops: list[CropTrace] = field(default_factory=list)
