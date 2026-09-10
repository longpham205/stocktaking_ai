"""Unit tests for src.validation.evaluator.Evaluator (VAL Stages 1-3)."""

from __future__ import annotations

from src.models.models import (
    BoundingBox,
    CropImage,
    CropTrace,
    Detection,
    DetectionResult,
    DecisionResult,
    InventoryItem,
    InventoryResult,
    OverlapResult,
    PipelineTrace,
    PluginResult,
    RefinedBox,
    RefinementResult,
    RetrievalCandidate,
    RetrievalResult,
)
from src.validation.evaluator import Evaluator, GroundTruthObject, ImageEvalInput


def _simple_record(
    image_key: str,
    pred_boxes: list[tuple[float, float, float, float]],
    pred_ids: list[str],
    gt_boxes: list[tuple[float, float, float, float]],
    gt_ids: list[str],
    iscrowd: list[bool] | None = None,
) -> ImageEvalInput:
    """Builds a minimal ImageEvalInput (Detection/End-to-End only, empty crops)."""
    detections = [Detection(bbox=BoundingBox(*box), confidence=0.9) for box in pred_boxes]
    detection_result = DetectionResult(image_id=image_key, detections=detections)

    items = [
        InventoryItem(
            product_id=pid, product_name=pid, bbox=BoundingBox(*box),
            detection_confidence=0.9, similarity_score=0.9, final_confidence=0.9, status="accepted",
        )
        for box, pid in zip(pred_boxes, pred_ids)
    ]
    result = InventoryResult(image_id=image_key, source_path=f"{image_key}.jpg", items=items, total_items=len(items))

    trace = PipelineTrace(
        image_id=image_key,
        detection_result=detection_result,
        overlap_result=OverlapResult(image_id=image_key),
        refinement_result=RefinementResult(image_id=image_key),
        crops=[],
    )

    iscrowd = iscrowd or [False] * len(gt_boxes)
    ground_truth = [
        GroundTruthObject(product_id=pid, bbox=BoundingBox(*box), iscrowd=crowd)
        for box, pid, crowd in zip(gt_boxes, gt_ids, iscrowd)
    ]

    return ImageEvalInput(
        image_key=image_key, source_path=f"{image_key}.jpg", result=result, trace=trace, ground_truth=ground_truth
    )


def _full_record_with_crop(
    image_key: str,
    gt_product_id: str,
    candidates: list[tuple[str, float]],
    decision_status: str,
    needs_plugin: bool,
    final_product_id: str | None,
    final_status: str,
    executed_plugins: list[str] | None = None,
) -> ImageEvalInput:
    """Build an ImageEvalInput with a full CropTrace for downstream-stage tests.

    The CropImage fixture intentionally contains both:
    - image_array: resized crop used by Retrieval.
    - raw_image_array: original-resolution crop used by plugins.
    """
    import numpy as np

    bbox = BoundingBox(0, 0, 100, 100)
    detection = Detection(bbox=bbox, confidence=0.9)
    detection_result = DetectionResult(
        image_id=image_key,
        detections=[detection],
    )

    # Retrieval input: fixed-size / resized representation.
    image_array = np.zeros(
        (10, 10, 3),
        dtype=np.uint8,
    )

    # Plugin input: original-resolution representation.
    # Deliberately keep a different size to reflect the CropImage contract.
    raw_image_array = np.zeros(
        (100, 100, 3),
        dtype=np.uint8,
    )

    crop = CropImage(
        crop_id="c1",
        image_id=image_key,
        image_array=image_array,
        raw_image_array=raw_image_array,
        source_bbox=bbox,
        detection_confidence=0.9,
        detection_index=0,
        used_refined_bbox=False,
    )

    retrieval_result = RetrievalResult(
        crop_id="c1",
        candidates=[
            RetrievalCandidate(
                product_id=pid,
                product_name=pid,
                similarity_score=sim,
                rank=i + 1,
            )
            for i, (pid, sim) in enumerate(candidates)
        ],
        detection_confidence=0.9,
    )

    decision_result = DecisionResult(
        crop_id="c1",
        product_id=candidates[0][0] if candidates else None,
        product_name=candidates[0][0] if candidates else None,
        detection_confidence=0.9,
        similarity_score=candidates[0][1] if candidates else 0.0,
        final_confidence=0.7,
        status=decision_status,
        needs_plugin=needs_plugin,
        trigger_reasons=(
            frozenset({"uncertain"})
            if needs_plugin
            else frozenset()
        ),
        processing_time_ms=0.1,
    )

    plugin_result = (
        PluginResult(
            crop_id="c1",
            executed_plugins=executed_plugins,
            evidence={},
            processing_time_ms=0.2,
        )
        if executed_plugins
        else None
    )

    final_decision = DecisionResult(
        crop_id="c1",
        product_id=final_product_id,
        product_name=final_product_id,
        detection_confidence=0.9,
        similarity_score=candidates[0][1] if candidates else 0.0,
        final_confidence=0.8,
        status=final_status,
        needs_plugin=False,
        rerank_latency_ms=0.05,
    )

    items = (
        [
            InventoryItem(
                product_id=final_product_id,
                product_name=final_product_id,
                bbox=bbox,
                detection_confidence=0.9,
                similarity_score=0.9,
                final_confidence=0.8,
                status=final_status,
            )
        ]
        if final_status == "accepted" and final_product_id
        else []
    )

    result = InventoryResult(
        image_id=image_key,
        source_path=f"{image_key}.jpg",
        items=items,
        total_items=len(items),
    )

    trace = PipelineTrace(
        image_id=image_key,
        detection_result=detection_result,
        overlap_result=OverlapResult(image_id=image_key),
        refinement_result=RefinementResult(image_id=image_key),
        crops=[
            CropTrace(
                crop=crop,
                retrieval_result=retrieval_result,
                decision_result=decision_result,
                plugin_result=plugin_result,
                final_decision=final_decision,
            )
        ],
    )

    return ImageEvalInput(
        image_key=image_key,
        source_path=f"{image_key}.jpg",
        result=result,
        trace=trace,
        ground_truth=[
            GroundTruthObject(
                product_id=gt_product_id,
                bbox=bbox,
            )
        ],
    )

def test_evaluator_perfect_detection_and_end_to_end(test_config) -> None:
    """Identical predicted and ground-truth boxes/ids must yield perfect scores."""
    record = _simple_record("img1", [(0, 0, 100, 100)], ["1"], [(0, 0, 100, 100)], ["1"])
    report = Evaluator(test_config).evaluate([record])

    assert report["detection"]["precision"] == 1.0
    assert report["end_to_end"]["precision"] == 1.0


def test_evaluator_detection_is_class_agnostic(test_config) -> None:
    """Detection stage must match by bbox alone, ignoring product_id mismatches."""
    record = _simple_record("img2", [(0, 0, 100, 100)], ["1"], [(0, 0, 100, 100)], ["999"])
    report = Evaluator(test_config).evaluate([record])

    assert report["detection"]["precision"] == 1.0
    assert report["end_to_end"]["precision"] == 0.0


def test_evaluator_crowd_annotations_excluded_but_counted(test_config) -> None:
    """iscrowd=1 GT must not count toward 1-to-1 detection matching, but is tracked."""
    record = _simple_record("img3", [], [], [(0, 0, 100, 100)], ["1"], iscrowd=[True])
    report = Evaluator(test_config).evaluate([record])

    assert report["detection"]["gt_objects"] == 0
    assert report["detection"]["gt_crowd_objects"] == 1
    assert report["detection"]["false_negative"] == 0


def test_evaluator_disabled_stage_reports_skipped(test_config) -> None:
    """A stage disabled via config must report 'skipped_by_config'."""
    stages = test_config.validation.stages.model_copy(update={"plugins": False})
    validation = test_config.validation.model_copy(update={"stages": stages})
    config = test_config.model_copy(update={"validation": validation})

    record = _simple_record("img4", [], [], [], [])
    report = Evaluator(config).evaluate([record])

    assert report["plugins"] == "skipped_by_config"
    assert report["detection"] != "skipped_by_config"


def test_evaluator_retrieval_rank_and_topk(test_config) -> None:
    """Retrieval stage must correctly compute GT rank and Top-K hit."""
    record = _full_record_with_crop(
        "img5", gt_product_id="2",
        candidates=[("1", 0.9), ("2", 0.85), ("3", 0.5)],
        decision_status="uncertain", needs_plugin=False,
        final_product_id="1", final_status="uncertain",
    )
    report = Evaluator(test_config).evaluate([record])

    assert report["retrieval"]["evaluated_crops"] == 1
    assert report["retrieval"]["top1_accuracy"] == 0.0  # GT was rank 2, not 1
    assert report["retrieval"]["topk_accuracy"] == 1.0  # but present in Top-K


def test_evaluator_decision_stage_false_acceptance(test_config) -> None:
    """An accepted wrong product must count as incorrect_accepted (false acceptance)."""
    record = _full_record_with_crop(
        "img6", gt_product_id="1",
        candidates=[("2", 0.9)],
        decision_status="accepted", needs_plugin=False,
        final_product_id="2", final_status="accepted",
    )
    report = Evaluator(test_config).evaluate([record])

    assert report["decision"]["incorrect_accepted"] == 1
    assert report["decision"]["correct_accepted"] == 0


def test_evaluator_fusion_corrects_a_wrong_decision(test_config) -> None:
    """Fusion stage must count a wrong-then-right transition as 'corrected'."""
    record = _full_record_with_crop(
        "img7", gt_product_id="2",
        candidates=[("1", 0.9), ("2", 0.85)],
        decision_status="uncertain", needs_plugin=True,
        final_product_id="2", final_status="accepted",
        executed_plugins=["barcode"],
    )
    report = Evaluator(test_config).evaluate([record])

    assert report["fusion"]["corrected_cases"] == 1
    assert report["fusion"]["accuracy_before"] == 0.0
    assert report["fusion"]["accuracy_after"] == 1.0


def test_evaluator_plugins_stage_tracks_execution(test_config) -> None:
    """Plugins stage must report trigger/execution counts per plugin name."""
    record = _full_record_with_crop(
        "img8", gt_product_id="1",
        candidates=[("1", 0.5)],
        decision_status="uncertain", needs_plugin=True,
        final_product_id="1", final_status="accepted",
        executed_plugins=["barcode"],
    )
    report = Evaluator(test_config).evaluate([record])

    assert report["plugins"]["barcode"]["trigger_rate"] == 1.0
    assert report["plugins"]["ocr"]["trigger_rate"] == 0.0


def test_evaluator_records_table_has_fn_row_for_missed_gt(test_config) -> None:
    """A fully missed GT object must produce a dedicated FN row in records."""
    record = _simple_record("img9", [], [], [(0, 0, 100, 100)], ["1"])
    report = Evaluator(test_config).evaluate([record])

    fn_rows = [r for r in report["records"] if r["gt_matched"] is False and r["crop_id"] is None]
    assert len(fn_rows) == 1
    assert fn_rows[0]["gt_product_id"] == "1"


def test_evaluator_per_product_breakdown(test_config) -> None:
    """per_product must report per-product_id TP/FP/FN."""
    record = _simple_record(
        "img10",
        pred_boxes=[(0, 0, 50, 50), (100, 100, 150, 150)],
        pred_ids=["1", "2"],
        gt_boxes=[(0, 0, 50, 50)],
        gt_ids=["1"],
    )
    report = Evaluator(test_config).evaluate([record])

    per_product = {entry["product_id"]: entry for entry in report["per_product"]}
    assert per_product["1"]["true_positive"] == 1
    assert per_product["2"]["false_positive"] == 1


def test_evaluator_overlap_stage_true_negative(test_config) -> None:
    """No suspicious pairs in GT and no flags from OverlapResolver must report zero TP/FP/FN."""
    record = _simple_record("img11", [(0, 0, 50, 50)], ["1"], [(0, 0, 50, 50)], ["1"])
    report = Evaluator(test_config).evaluate([record])

    assert report["overlap"]["true_positive"] == 0
    assert report["overlap"]["false_positive"] == 0


def test_evaluator_segmentation_stage_reports_supported_flags(test_config) -> None:
    """Segmentation stage must always report supported/enabled/used flags."""
    record = _simple_record("img12", [], [], [], [])
    report = Evaluator(test_config).evaluate([record])

    assert report["segmentation"]["segmentation_supported"] is True
    assert "segmentation_used" in report["segmentation"]

def test_crop_image_keeps_resized_and_raw_resolution(test_config) -> None:
    """CropImage must preserve separate retrieval and raw-resolution arrays."""
    import numpy as np

    record = _full_record_with_crop(
        "img_crop",
        gt_product_id="1",
        candidates=[("1", 0.9)],
        decision_status="accepted",
        needs_plugin=False,
        final_product_id="1",
        final_status="accepted",
    )

    crop = record.trace.crops[0].crop

    assert crop.image_array.shape == (10, 10, 3)
    assert crop.raw_image_array.shape == (100, 100, 3)
    assert crop.source_bbox == BoundingBox(0, 0, 100, 100)
    assert crop.detection_index == 0
    assert crop.used_refined_bbox is False