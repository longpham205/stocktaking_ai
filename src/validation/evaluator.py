"""Benchmark evaluation metric computation module (VAL).

Computes quantitative metrics by comparing real `InventoryPipeline`
predictions (via `PipelineTrace`) against COCO-style ground truth. Never
reruns or simplifies the pipeline itself (VAL spec, Section 15).

Every stage's *formula* lives in `src/validation/metrics.py`
(`METRIC_REGISTRY`); this module is responsible only for **gathering**
the raw per-stage statistics (tp/fp/fn counts, IoU lists, rank lists,
...) from a `PipelineTrace` + ground truth, then asking `metrics.py` to
compute whichever metric names are enabled in
`configs/config.yaml -> validation.metrics.<stage>`. Adding a new metric
therefore never requires touching this file (see metrics.py docstring).

Core matching convention (project owner decisions):
    - Detection is evaluated class-agnostically by bounding-box IoU only;
      product identity is Retrieval's responsibility.
    - Matching uses greedy IoU assignment (not Hungarian).
    - COCO `iscrowd=1` annotations are excluded from 1-to-1 matching but
      are still counted separately.
    - End-to-End matching additionally requires product_id equality.
    - A single detection<->GT match is computed once per image and reused
      by every downstream stage (Cropping/Retrieval/Decision/Plugins/
      Fusion), so all stages agree on "which crop corresponds to which
      GT object" (VAL spec, Section 15).
    - Overlap "ground truth" is derived, not annotated: a GT pair is
      treated as a true overlap case when the SAME suspicious-pair
      formula OverlapResolver uses (`find_suspicious_pairs`) flags it
      when applied to the GT boxes themselves. Pairs where either
      detection did not match a GT object are excluded (unverifiable).

Per-stage enable/disable and metric selection come entirely from
`configs/config.yaml -> validation.stages` / `validation.metrics`. A
disabled stage reports the literal string "skipped_by_config".
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.core.config import AppConfig
from src.core.logger import get_logger
from src.models.models import (
    BoundingBox,
    CropTrace,
    Detection,
    InventoryResult,
    PipelineTrace,
)
from src.pipeline.overlap import find_suspicious_pairs
from src.validation.metrics import compute_metrics

logger = get_logger(__name__)


@dataclass
class GroundTruthObject:
    """A single ground-truth object instance for one benchmark image.

    Attributes:
        product_id: Stable internal product_id (string), resolved from
            COCO `category_id` (see ValidationRunner).
        bbox: Ground-truth bounding box in [x1, y1, x2, y2] pixel coords.
        iscrowd: COCO `iscrowd` flag. Crowd annotations are excluded from
            1-to-1 matching but still counted.
    """

    product_id: str
    bbox: BoundingBox
    iscrowd: bool = False


@dataclass
class ImageEvalInput:
    """Everything the Evaluator needs for one benchmark image.

    Attributes:
        image_key: Stable key for this image (filename stem).
        source_path: Filesystem path of the benchmark image.
        result: Final InventoryResult produced by the pipeline.
        trace: Full intermediate-stage trace from `run_with_trace`.
        ground_truth: Ground-truth object instances for this image.
    """

    image_key: str
    source_path: str
    result: InventoryResult
    trace: PipelineTrace
    ground_truth: list[GroundTruthObject] = field(default_factory=list)


_SKIPPED = "skipped_by_config"


@dataclass
class _ImageContext:
    """Precomputed detection<->GT matching for one image, shared by every stage."""

    record: ImageEvalInput
    non_crowd_gt: list[GroundTruthObject]
    crowd_gt: list[GroundTruthObject]
    matches: list[tuple[int, int, float]]  # (detection_index, gt_index, iou)
    unmatched_detection_indices: list[int]
    unmatched_gt_indices: list[int]
    detection_to_gt: dict[int, int]
    gt_to_detection: dict[int, int]
    crop_by_detection_index: dict[int, CropTrace]


class Evaluator:
    """Computes staged VAL metrics from real pipeline traces + ground truth."""

    def __init__(self, config: AppConfig) -> None:
        """Initializes the Evaluator.

        Args:
            config: Fully validated application configuration. Reads
                `validation.iou_match_threshold`, `validation.stages`,
                and `validation.metrics`.
        """
        self._iou_threshold = config.validation.iou_match_threshold
        self._top_k = config.validation.top_k
        self._stages = config.validation.stages
        self._metric_names = config.validation.metrics
        self._overlap_trigger = config.refinement.trigger
        self._refinement_enabled = config.refinement.enabled
        self._refinement_backend = config.refinement.backend

    def evaluate(self, records: list[ImageEvalInput]) -> dict:
        """Evaluates a full benchmark run and produces the VAL report.

        Args:
            records: One ImageEvalInput per benchmark image.

        Returns:
            A dictionary matching the VAL Section 14 output schema, plus
            a top-level "records" list (flat per-crop table) for export.
        """
        contexts = [self._build_context(record) for record in records]

        detection_report = self._evaluate_detection(contexts)
        cropping_report = self._evaluate_cropping(contexts)
        overlap_report = self._evaluate_overlap(contexts)
        segmentation_report = self._evaluate_segmentation(contexts)
        retrieval_report = self._evaluate_retrieval(contexts)
        decision_report = self._evaluate_decision(contexts)
        plugins_report = self._evaluate_plugins(contexts)
        fusion_report = self._evaluate_fusion(contexts)
        end_to_end_report = self._evaluate_end_to_end(contexts)
        records_table = self._build_records(contexts)

        report = {
            "dataset": {"total_images": len(records)},
            "configuration": {
                "iou_match_threshold": self._iou_threshold,
                "top_k": self._top_k,
                "stages_enabled": self._stages.model_dump(),
            },
            "overall": {
                "detection_f1": _metric_or_na(detection_report, "f1"),
                "end_to_end_f1": _metric_or_na(end_to_end_report, "f1"),
            },
            "detection": detection_report,
            "cropping": cropping_report,
            "overlap": overlap_report,
            "segmentation": segmentation_report,
            "retrieval": retrieval_report,
            "decision": decision_report,
            "plugins": plugins_report,
            "fusion": fusion_report,
            "end_to_end": end_to_end_report,
            "per_image": self._per_image_summary(contexts),
            "per_product": self._per_product_summary(end_to_end_report),
            "errors": self._collect_pipeline_attribution(contexts),
            "latency": self._evaluate_latency(contexts),
            "records": records_table,
        }

        logger.info("Evaluator completed: %d image(s) evaluated.", len(records))
        return report

    # =========================================================================
    # Context building (shared detection<->GT matching)
    # =========================================================================

    def _build_context(self, record: ImageEvalInput) -> _ImageContext:
        """Computes the shared detection<->GT match for one image.

        Args:
            record: One ImageEvalInput.

        Returns:
            An _ImageContext reused by every stage evaluator.
        """
        non_crowd_gt = [gt for gt in record.ground_truth if not gt.iscrowd]
        crowd_gt = [gt for gt in record.ground_truth if gt.iscrowd]

        pred_boxes = [detection.bbox for detection in record.trace.detection_result.detections]
        gt_boxes = [gt.bbox for gt in non_crowd_gt]

        matches, unmatched_pred, unmatched_gt = greedy_iou_match(pred_boxes, gt_boxes, self._iou_threshold)

        detection_to_gt = {det_idx: gt_idx for det_idx, gt_idx, _ in matches}
        gt_to_detection = {gt_idx: det_idx for det_idx, gt_idx, _ in matches}
        crop_by_detection_index = {crop_trace.crop.detection_index: crop_trace for crop_trace in record.trace.crops}

        return _ImageContext(
            record=record,
            non_crowd_gt=non_crowd_gt,
            crowd_gt=crowd_gt,
            matches=matches,
            unmatched_detection_indices=unmatched_pred,
            unmatched_gt_indices=unmatched_gt,
            detection_to_gt=detection_to_gt,
            gt_to_detection=gt_to_detection,
            crop_by_detection_index=crop_by_detection_index,
        )

    # =========================================================================
    # Detection stage (Section 3) - class-agnostic bbox IoU matching
    # =========================================================================

    def _evaluate_detection(self, contexts: list[_ImageContext]):
        if not self._stages.detection:
            return _SKIPPED

        stats = {"tp": 0, "fp": 0, "fn": 0, "iou_values": [], "latencies": []}
        gt_total = gt_crowd_total = pred_total = 0
        confidences: list[float] = []

        for context in contexts:
            stats["tp"] += len(context.matches)
            stats["fp"] += len(context.unmatched_detection_indices)
            stats["fn"] += len(context.unmatched_gt_indices)
            stats["iou_values"].extend(iou for _, _, iou in context.matches)
            stats["latencies"].append(context.record.trace.detection_result.processing_time_ms)

            gt_total += len(context.non_crowd_gt)
            gt_crowd_total += len(context.crowd_gt)
            pred_total += len(context.record.trace.detection_result.detections)
            confidences.extend(d.confidence for d in context.record.trace.detection_result.detections)

        result = compute_metrics(stats, self._metric_names.detection)
        result.update(
            {
                "gt_objects": gt_total,
                "gt_crowd_objects": gt_crowd_total,
                "predicted_objects": pred_total,
                "true_positive": stats["tp"],
                "false_positive": stats["fp"],
                "false_negative": stats["fn"],
                "per_class_ap": "N/A (detection is class-agnostic; product identity is evaluated at Retrieval)",
                "confidence_mean": round(_mean(confidences), 4),
                "confidence_min": round(min(confidences), 4) if confidences else 0.0,
                "confidence_max": round(max(confidences), 4) if confidences else 0.0,
                "detection_mode": "bbox",
                "segmentation_supported": True,
                "segmentation_enabled": self._refinement_enabled,
                "segmentation_used": any(c.record.trace.refinement_result.triggered for c in contexts),
            }
        )
        return result

    # =========================================================================
    # Cropping stage (Section 6)
    # =========================================================================

    def _evaluate_cropping(self, contexts: list[_ImageContext]):
        if not self._stages.cropping:
            return _SKIPPED

        stats = {"valid": 0, "total": 0, "iou_values": [], "latencies": []}
        invalid = 0

        for context in contexts:
            for detection_index, gt_index in context.detection_to_gt.items():
                stats["total"] += 1
                crop_trace = context.crop_by_detection_index.get(detection_index)
                if crop_trace is None:
                    invalid += 1
                    continue
                stats["valid"] += 1
                gt_box = context.non_crowd_gt[gt_index].bbox
                stats["iou_values"].append(crop_trace.crop.source_bbox.iou(gt_box))

        result = compute_metrics(stats, self._metric_names.cropping)
        result.update(
            {
                "valid_crop_count": stats["valid"],
                "invalid_crop_count": invalid,
                "crop_to_gt_matching_rate": round(_safe_divide(stats["valid"], stats["total"]), 4),
            }
        )
        return result

    # =========================================================================
    # Overlap stage (Section 4)
    # =========================================================================

    def _evaluate_overlap(self, contexts: list[_ImageContext]):
        if not self._stages.overlap:
            return _SKIPPED

        stats = {"tp": 0, "fp": 0, "fn": 0, "latencies": []}
        sent_to_refiner = correctly_unrefined = 0

        for context in contexts:
            stats["latencies"].append(context.record.trace.overlap_result.processing_time_ms)

            gt_boxes = [gt.bbox for gt in context.non_crowd_gt]
            fake_gt_detections = [Detection(bbox=box, confidence=1.0) for box in gt_boxes]
            true_pairs = {
                (min(p.detection_index_a, p.detection_index_b), max(p.detection_index_a, p.detection_index_b))
                for p in find_suspicious_pairs(
                    fake_gt_detections,
                    self._overlap_trigger.iou_threshold,
                    self._overlap_trigger.overlap_ratio_threshold,
                )
            }
            true_pairs_as_detections = set()
            for gt_a, gt_b in true_pairs:
                det_a, det_b = context.gt_to_detection.get(gt_a), context.gt_to_detection.get(gt_b)
                if det_a is not None and det_b is not None:
                    true_pairs_as_detections.add((min(det_a, det_b), max(det_a, det_b)))

            flagged_pairs = {
                (min(p.detection_index_a, p.detection_index_b), max(p.detection_index_a, p.detection_index_b))
                for p in context.record.trace.overlap_result.pairs
            }
            verifiable_flagged = {
                pair
                for pair in flagged_pairs
                if pair[0] in context.detection_to_gt and pair[1] in context.detection_to_gt
            }

            stats["tp"] += len(true_pairs_as_detections & verifiable_flagged)
            stats["fn"] += len(true_pairs_as_detections - verifiable_flagged)
            stats["fp"] += len(verifiable_flagged - true_pairs_as_detections)

            if context.record.trace.overlap_result.needs_refinement:
                sent_to_refiner += 1
            elif not true_pairs_as_detections:
                correctly_unrefined += 1

        result = compute_metrics(stats, self._metric_names.overlap)
        result.update(
            {
                "true_positive": stats["tp"],
                "false_positive": stats["fp"],
                "false_negative": stats["fn"],
                "sent_to_refiner_count": sent_to_refiner,
                "correctly_left_unrefined_count": correctly_unrefined,
                "note": (
                    "GT overlap pairs are derived from GT box geometry using the same formula as "
                    "OverlapResolver, not separately annotated; pairs where either side has no matched "
                    "detection are excluded as unverifiable."
                ),
            }
        )
        return result

    # =========================================================================
    # Segmentation stage (Section 5)
    # =========================================================================

    def _evaluate_segmentation(self, contexts: list[_ImageContext]):
        if not self._stages.segmentation:
            return _SKIPPED

        stats = {"iou_before": [], "iou_after": [], "improved": 0, "total": 0, "latencies": []}
        success = failure = 0

        for context in contexts:
            stats["latencies"].append(context.record.trace.refinement_result.processing_time_ms)

            for refined_box in context.record.trace.refinement_result.refined_boxes:
                if refined_box.used_fallback:
                    failure += 1
                    continue
                success += 1

                gt_index = context.detection_to_gt.get(refined_box.detection_index)
                if gt_index is None:
                    continue
                gt_box = context.non_crowd_gt[gt_index].bbox
                original_bbox = context.record.trace.detection_result.detections[refined_box.detection_index].bbox

                iou_before = original_bbox.iou(gt_box)
                iou_after = refined_box.refined_bbox.iou(gt_box)
                stats["iou_before"].append(iou_before)
                stats["iou_after"].append(iou_after)
                stats["total"] += 1
                if iou_after > iou_before:
                    stats["improved"] += 1

        result = compute_metrics(stats, self._metric_names.segmentation)
        result.update(
            {
                "refinement_triggered_count": sum(1 for c in contexts if c.record.trace.refinement_result.triggered),
                "refinement_success_count": success,
                "refinement_failure_count": failure,
                "degraded_count": sum(1 for b, a in zip(stats["iou_before"], stats["iou_after"]) if a < b),
                "unchanged_count": sum(1 for b, a in zip(stats["iou_before"], stats["iou_after"]) if a == b),
                "segmentation_supported": True,
                "segmentation_enabled": self._refinement_enabled,
                "segmentation_used": self._refinement_backend != "none",
            }
        )
        return result

    # =========================================================================
    # Retrieval stage (Section 7)
    # =========================================================================

    def _evaluate_retrieval(self, contexts: list[_ImageContext]):
        if not self._stages.retrieval:
            return _SKIPPED

        stats = {"ranks": [], "top_k": self._top_k, "latencies": []}
        similarities: list[float] = []
        per_product_hits: dict[str, list[bool]] = {}

        for context in contexts:
            for detection_index, gt_index in context.detection_to_gt.items():
                crop_trace = context.crop_by_detection_index.get(detection_index)
                if crop_trace is None:
                    continue
                gt_product_id = context.non_crowd_gt[gt_index].product_id
                retrieval_result = crop_trace.retrieval_result
                stats["latencies"].append(retrieval_result.processing_time_ms)

                rank = next((c.rank for c in retrieval_result.candidates if c.product_id == gt_product_id), None)
                stats["ranks"].append(rank)

                if retrieval_result.top_candidate is not None:
                    similarities.append(retrieval_result.top_candidate.similarity_score)

                per_product_hits.setdefault(gt_product_id, []).append(rank == 1)

        result = compute_metrics(stats, self._metric_names.retrieval)
        result.update(
            {
                "evaluated_crops": len(stats["ranks"]),
                "mean_top1_similarity": round(_mean(similarities), 4),
                "per_product_top1_accuracy": {
                    pid: round(_safe_divide(sum(hits), len(hits)), 4) for pid, hits in per_product_hits.items()
                },
            }
        )
        return result

    # =========================================================================
    # Decision stage (Section 8)
    # =========================================================================

    def _evaluate_decision(self, contexts: list[_ImageContext]):
        if not self._stages.decision:
            return _SKIPPED

        stats = {"tp": 0, "fp": 0, "fn": 0, "correct": 0, "total": 0, "pairs": [], "latencies": []}
        ambiguous_count = uncertain_count = 0

        for context in contexts:
            for detection_index, gt_index in context.detection_to_gt.items():
                crop_trace = context.crop_by_detection_index.get(detection_index)
                if crop_trace is None:
                    continue
                gt_product_id = context.non_crowd_gt[gt_index].product_id
                decision = crop_trace.decision_result
                stats["latencies"].append(decision.processing_time_ms)

                is_accept = decision.status == "accepted"
                is_right_product = decision.product_id == gt_product_id

                if is_accept and is_right_product:
                    stats["tp"] += 1
                elif is_accept and not is_right_product:
                    stats["fp"] += 1
                elif not is_accept and is_right_product:
                    stats["fn"] += 1

                stats["total"] += 1
                stats["correct"] += int(is_accept == is_right_product)
                stats["pairs"].append(("correct" if is_right_product else "incorrect", decision.status))

                if "uncertain" in decision.trigger_reasons:
                    uncertain_count += 1
                if "ambiguous" in decision.trigger_reasons:
                    ambiguous_count += 1

        result = compute_metrics(stats, self._metric_names.decision)
        result.update(
            {
                "correct_accepted": stats["tp"],
                "incorrect_accepted": stats["fp"],
                "false_rejection": stats["fn"],
                "uncertain_case_count": uncertain_count,
                "ambiguous_case_count": ambiguous_count,
            }
        )
        return result

    # =========================================================================
    # Plugins stage (Section 9)
    # =========================================================================

    def _evaluate_plugins(self, contexts: list[_ImageContext]):
        if not self._stages.plugins:
            return _SKIPPED

        plugin_names = ("ocr", "color", "barcode")
        per_plugin: dict[str, dict] = {
            name: {
                "eligible": 0, "triggered": 0, "executed": 0,
                "success": 0, "correct": 0, "total": 0,
                "corrected": 0, "degraded": 0, "influenced": 0,
                "latencies": [],
            }
            for name in plugin_names
        }

        for context in contexts:
            for detection_index, gt_index in context.detection_to_gt.items():
                crop_trace = context.crop_by_detection_index.get(detection_index)
                if crop_trace is None or not crop_trace.decision_result.needs_plugin:
                    continue
                gt_product_id = context.non_crowd_gt[gt_index].product_id

                for name in plugin_names:
                    per_plugin[name]["eligible"] += 1

                plugin_result = crop_trace.plugin_result
                if plugin_result is None:
                    continue

                rerank_debug = getattr(crop_trace.final_decision, "rerank_debug", None) or {}
                candidates = rerank_debug.get("candidates", [])
                final_winner_id = crop_trace.final_decision.product_id
                decision_before_correct = crop_trace.decision_result.product_id == gt_product_id
                decision_after_correct = crop_trace.final_decision.product_id == gt_product_id

                for name in plugin_names:
                    if name not in plugin_result.executed_plugins:
                        continue
                    stats = per_plugin[name]
                    stats["triggered"] += 1
                    stats["executed"] += 1

                    evidence = plugin_result.evidence.get(name, {})
                    latency = evidence.get("latency_ms")
                    if latency is not None:
                        stats["latencies"].append(float(latency))

                    if _plugin_success(name, evidence):
                        stats["success"] += 1

                    # Which candidate did THIS plugin's evidence point to?
                    identified_product_id, identified_strength = _plugin_identified_candidate(
                        name, candidates
                    )

                    if identified_product_id is not None and identified_strength > 0.0:
                        stats["total"] += 1
                        if str(identified_product_id) == str(gt_product_id):
                            stats["correct"] += 1

                    # Did this plugin's evidence agree with the FINAL winner,
                    # and did the decision flip correctness as a result?
                    plugin_supported_winner = (
                        identified_product_id is not None
                        and str(identified_product_id) == str(final_winner_id)
                    )
                    if decision_before_correct != decision_after_correct and plugin_supported_winner:
                        stats["influenced"] += 1
                        if decision_after_correct:
                            stats["corrected"] += 1
                        else:
                            stats["degraded"] += 1

        result = {}
        for name, stats in per_plugin.items():
            metrics_out = compute_metrics(stats, self._metric_names.plugins)
            metrics_out.update(
                {
                    "supported": True,
                    "eligible": stats["eligible"],
                    "executed": stats["executed"],
                    "skipped": stats["eligible"] - stats["executed"],
                    "success": stats["success"],
                    "failure": stats["executed"] - stats["success"],
                    "identified_total": stats["total"],
                    "identified_correct": stats["correct"],
                    "corrected": stats["corrected"],
                    "degraded": stats["degraded"],
                    "influenced": stats["influenced"],
                }
            )
            result[name] = metrics_out
        return result

    # =========================================================================
    # Fusion stage (Section 10)
    # =========================================================================

    def _evaluate_fusion(self, contexts: list[_ImageContext]):
        if not self._stages.fusion:
            return _SKIPPED

        stats = {
            "correct_before": 0,
            "correct_after": 0,
            "total": 0,
            "corrected": 0,
            "degraded": 0,
            "executed": 0,
            "latencies": [],
        }

        for context in contexts:
            for detection_index, gt_index in context.detection_to_gt.items():
                crop_trace = context.crop_by_detection_index.get(detection_index)
                if crop_trace is None or not crop_trace.decision_result.needs_plugin:
                    continue

                gt_product_id = context.non_crowd_gt[gt_index].product_id
                before_correct = crop_trace.decision_result.product_id == gt_product_id
                after_correct = crop_trace.final_decision.product_id == gt_product_id

                stats["total"] += 1
                stats["executed"] += 1
                stats["correct_before"] += int(before_correct)
                stats["correct_after"] += int(after_correct)
                stats["corrected"] += int((not before_correct) and after_correct)
                stats["degraded"] += int(before_correct and (not after_correct))
                stats["latencies"].append(crop_trace.final_decision.rerank_latency_ms)

        result = compute_metrics(stats, self._metric_names.fusion)
        result.update(
            {
                "corrected_cases": stats["corrected"],
                "newly_incorrect_cases": stats["degraded"],
                "unchanged_cases": stats["executed"] - stats["corrected"] - stats["degraded"],
                "evaluated_cases": stats["executed"],
                "mean_fusion_latency_ms": round(_mean(stats["latencies"]), 3),
            }
        )
        return result

    # =========================================================================
    # End-to-End (Section 11)
    # =========================================================================

    def _evaluate_end_to_end(self, contexts: list[_ImageContext]):
        if not self._stages.end_to_end:
            return _SKIPPED

        stats = {"tp": 0, "fp": 0, "fn": 0, "pairs": [], "latencies": []}
        exact_count_matches = 0
        per_product_counts: dict[str, dict[str, int]] = {}

        for context in contexts:
            pred_items = context.record.result.items
            pred_boxes = [item.bbox for item in pred_items]
            pred_ids = [item.product_id for item in pred_items]
            gt_boxes = [gt.bbox for gt in context.non_crowd_gt]
            gt_ids = [gt.product_id for gt in context.non_crowd_gt]

            def class_ok(pred_index: int, gt_index: int, _pred_ids=pred_ids, _gt_ids=gt_ids) -> bool:
                return _pred_ids[pred_index] == _gt_ids[gt_index]

            matches, unmatched_pred, unmatched_gt = greedy_iou_match(
                pred_boxes, gt_boxes, self._iou_threshold, class_ok=class_ok if pred_ids and gt_ids else None
            )
            bbox_matches, bbox_unmatched_pred, bbox_unmatched_gt = greedy_iou_match(
                pred_boxes, gt_boxes, self._iou_threshold
            )
            for pred_index, gt_index, _iou in bbox_matches:
                stats["pairs"].append((gt_ids[gt_index], pred_ids[pred_index]))
            for gt_index in bbox_unmatched_gt:
                stats["pairs"].append((gt_ids[gt_index], "MISSED"))
            for pred_index in bbox_unmatched_pred:
                stats["pairs"].append(("BACKGROUND", pred_ids[pred_index]))

            stats["tp"] += len(matches)
            stats["fp"] += len(unmatched_pred)
            stats["fn"] += len(unmatched_gt)
            stats["latencies"].append(context.record.result.processing_time_ms)

            if len(pred_items) == len(context.non_crowd_gt):
                exact_count_matches += 1

            for pred_index, gt_index, _iou in matches:
                per_product_counts.setdefault(pred_ids[pred_index], {"tp": 0, "fp": 0, "fn": 0})["tp"] += 1
            for pred_index in unmatched_pred:
                per_product_counts.setdefault(pred_ids[pred_index], {"tp": 0, "fp": 0, "fn": 0})["fp"] += 1
            for gt_index in unmatched_gt:
                per_product_counts.setdefault(gt_ids[gt_index], {"tp": 0, "fp": 0, "fn": 0})["fn"] += 1

        result = compute_metrics(stats, self._metric_names.end_to_end)
        per_product = []
        for product_id, counts in sorted(per_product_counts.items()):
            per_product.append(
                {
                    "product_id": product_id,
                    "true_positive": counts["tp"],
                    "false_positive": counts["fp"],
                    "false_negative": counts["fn"],
                    "precision": round(_safe_divide(counts["tp"], counts["tp"] + counts["fp"]), 4),
                    "recall": round(_safe_divide(counts["tp"], counts["tp"] + counts["fn"]), 4),
                }
            )
        result.update(
            {
                "true_positive": stats["tp"],
                "false_positive": stats["fp"],
                "false_negative": stats["fn"],
                "product_count_accuracy": round(_safe_divide(exact_count_matches, len(contexts)), 4),
                "mean_latency_ms": round(_mean(stats["latencies"]), 3),
                "per_product": per_product,
            }
        )
        return result

    # =========================================================================
    # Per-image / per-product summaries, latency, attribution
    # =========================================================================

    @staticmethod
    def _per_image_summary(contexts: list[_ImageContext]) -> list[dict]:
        """Builds a compact per-image summary row for the report."""
        rows = []
        for context in contexts:
            rows.append(
                {
                    "image_key": context.record.image_key,
                    "source_path": context.record.source_path,
                    "predicted_count": len(context.record.result.items),
                    "ground_truth_count": len(context.non_crowd_gt),
                    "detection_matched": len(context.matches),
                    "processing_time_ms": round(context.record.result.processing_time_ms, 3),
                }
            )
        return rows

    @staticmethod
    def _per_product_summary(end_to_end_report) -> list[dict]:
        """Extracts the per-product breakdown already computed by end-to-end."""
        if isinstance(end_to_end_report, dict):
            return end_to_end_report.get("per_product", [])
        return []

    @staticmethod
    def _collect_pipeline_attribution(contexts: list[_ImageContext]) -> dict:
        """Attributes coarse error counts to each pipeline stage (Section 12)."""
        detection_errors = sum(1 for c in contexts if c.unmatched_detection_indices or c.unmatched_gt_indices)
        end_to_end_errors = sum(1 for c in contexts if len(c.record.result.items) != len(c.non_crowd_gt))
        return {
            "DETECTION": detection_errors,
            "OVERLAP": "see overlap.false_positive / overlap.false_negative",
            "SEGMENTATION": "see segmentation.degraded_count",
            "CROPPING": "see cropping.invalid_crop_count",
            "RETRIEVAL": "see retrieval.per_product_top1_accuracy",
            "DECISION": "see decision.incorrect_accepted / false_rejection",
            "PLUGIN": "see plugins.<name>.failure",
            "FUSION": "see fusion.newly_incorrect_cases",
            "END_TO_END": end_to_end_errors,
        }

    @staticmethod
    def _evaluate_latency(contexts: list[_ImageContext]) -> dict:
        """Aggregates latency across every pipeline stage, per-image and overall."""
        stage_latencies = {
            "detection_ms": [c.record.trace.detection_result.processing_time_ms for c in contexts],
            "overlap_ms": [c.record.trace.overlap_result.processing_time_ms for c in contexts],
            "refinement_ms": [c.record.trace.refinement_result.processing_time_ms for c in contexts],
            "end_to_end_ms": [c.record.result.processing_time_ms for c in contexts],
            "retrieval_ms": [ct.retrieval_result.processing_time_ms for c in contexts for ct in c.record.trace.crops],
            "decision_ms": [ct.decision_result.processing_time_ms for c in contexts for ct in c.record.trace.crops],
            "plugin_ms": [
                ct.plugin_result.processing_time_ms for c in contexts for ct in c.record.trace.crops if ct.plugin_result
            ],
            "rerank_ms": [
                ct.final_decision.rerank_latency_ms for c in contexts for ct in c.record.trace.crops if ct.plugin_result
            ],
        }

        summary = {}
        for name, values in stage_latencies.items():
            summary[name] = {
                "mean": round(_mean(values), 3),
                "min": round(min(values), 3) if values else 0.0,
                "max": round(max(values), 3) if values else 0.0,
            }
        return summary

    # =========================================================================
    # Flat records table (for pandas / notebook / chart consumption)
    # =========================================================================

    @staticmethod
    def _build_records(contexts: list[_ImageContext]) -> list[dict]:
        """Builds the flat per-crop records table (see module docstring / config).

        One row per crop (matched or not), PLUS one row per fully-missed
        ground-truth object (`crop_id=None`), so recall computed directly
        from this table (without the aggregate report) is correct.

        Args:
            contexts: Precomputed per-image contexts.

        Returns:
            List of flat dict rows, ready for `pandas.DataFrame(rows)` or
            direct CSV export.
        """
        rows: list[dict] = []

        for context in contexts:
            for detection_index, detection in enumerate(context.record.trace.detection_result.detections):
                gt_index = context.detection_to_gt.get(detection_index)
                gt_product_id = context.non_crowd_gt[gt_index].product_id if gt_index is not None else None
                detection_iou_gt = next((iou for d, g, iou in context.matches if d == detection_index), None)

                crop_trace = context.crop_by_detection_index.get(detection_index)
                refined_box = context.record.trace.refinement_result.get_refined_box(detection_index)

                row = {
                    "image_key": context.record.image_key,
                    "source_path": context.record.source_path,
                    "detection_index": detection_index,
                    "crop_id": crop_trace.crop.crop_id if crop_trace else None,
                    "gt_product_id": gt_product_id,
                    "gt_matched": gt_index is not None,
                    "detection_confidence": round(detection.confidence, 4),
                    "detection_iou_gt": round(detection_iou_gt, 4) if detection_iou_gt is not None else None,
                    "overlap_flagged": any(
                        detection_index in (p.detection_index_a, p.detection_index_b)
                        for p in context.record.trace.overlap_result.pairs
                    ),
                    "refinement_triggered": context.record.trace.refinement_result.triggered,
                    "refinement_used_fallback": refined_box.used_fallback if refined_box else None,
                    "iou_before_refine": None,
                    "iou_after_refine": None,
                    "retrieval_top1_product_id": None,
                    "retrieval_top1_similarity": None,
                    "retrieval_gt_rank": None,
                    "decision_status_before": None,
                    "decision_trigger_reasons": None,
                    "plugins_executed": None,
                    "final_product_id": None,
                    "final_status": None,
                    "final_confidence": None,
                    "changed_by_plugin": False,
                    "detection_latency_ms": round(context.record.trace.detection_result.processing_time_ms, 3),
                    "retrieval_latency_ms": None,
                    "plugin_latency_ms": None,
                    "rerank_latency_ms": None,
                    "correct": False,
                }

                if refined_box is not None and not refined_box.used_fallback and gt_index is not None:
                    gt_box = context.non_crowd_gt[gt_index].bbox
                    row["iou_before_refine"] = round(detection.bbox.iou(gt_box), 4)
                    row["iou_after_refine"] = round(refined_box.refined_bbox.iou(gt_box), 4)

                if crop_trace is not None:
                    top1 = crop_trace.retrieval_result.top_candidate
                    row["retrieval_top1_product_id"] = top1.product_id if top1 else None
                    row["retrieval_top1_similarity"] = round(top1.similarity_score, 4) if top1 else None
                    if gt_product_id is not None:
                        rank = next(
                            (c.rank for c in crop_trace.retrieval_result.candidates if c.product_id == gt_product_id),
                            None,
                        )
                        row["retrieval_gt_rank"] = rank
                    row["decision_status_before"] = crop_trace.decision_result.status
                    row["decision_trigger_reasons"] = ",".join(sorted(crop_trace.decision_result.trigger_reasons))
                    row["plugins_executed"] = (
                        ",".join(crop_trace.plugin_result.executed_plugins) if crop_trace.plugin_result else ""
                    )
                    row["final_product_id"] = crop_trace.final_decision.product_id
                    row["final_status"] = crop_trace.final_decision.status
                    row["final_confidence"] = round(crop_trace.final_decision.final_confidence, 4)
                    row["changed_by_plugin"] = (
                        crop_trace.decision_result.product_id != crop_trace.final_decision.product_id
                    )
                    row["retrieval_latency_ms"] = round(crop_trace.retrieval_result.processing_time_ms, 3)
                    row["plugin_latency_ms"] = (
                        round(crop_trace.plugin_result.processing_time_ms, 3) if crop_trace.plugin_result else 0.0
                    )
                    row["rerank_latency_ms"] = round(crop_trace.final_decision.rerank_latency_ms, 3)
                    row["correct"] = (
                        crop_trace.final_decision.status == "accepted"
                        and crop_trace.final_decision.product_id == gt_product_id
                    )

                rows.append(row)

            for gt_index in context.unmatched_gt_indices:
                gt = context.non_crowd_gt[gt_index]
                rows.append(
                    {
                        "image_key": context.record.image_key,
                        "source_path": context.record.source_path,
                        "detection_index": None,
                        "crop_id": None,
                        "gt_product_id": gt.product_id,
                        "gt_matched": False,
                        "detection_confidence": None,
                        "detection_iou_gt": 0.0,
                        "overlap_flagged": False,
                        "refinement_triggered": None,
                        "refinement_used_fallback": None,
                        "iou_before_refine": None,
                        "iou_after_refine": None,
                        "retrieval_top1_product_id": None,
                        "retrieval_top1_similarity": None,
                        "retrieval_gt_rank": None,
                        "decision_status_before": None,
                        "decision_trigger_reasons": None,
                        "plugins_executed": None,
                        "final_product_id": None,
                        "final_status": "missed",
                        "final_confidence": None,
                        "changed_by_plugin": False,
                        "detection_latency_ms": None,
                        "retrieval_latency_ms": None,
                        "plugin_latency_ms": None,
                        "rerank_latency_ms": None,
                        "correct": False,
                    }
                )

        return rows


def _plugin_success(name: str, evidence: dict) -> bool:
    """Determines whether a plugin's evidence counts as "success" for VAL.

    Args:
        name: Plugin name ("ocr", "color", "barcode").
        evidence: The plugin's raw evidence dict.

    Returns:
        True if the evidence is considered usable.
    """
    if name == "ocr":
        return int(evidence.get("text_length", 0)) > 0
    if name == "barcode":
        return len(evidence.get("barcodes", [])) > 0
    if name == "color":
        return len(evidence.get("palette", [])) > 0
    return False

def _plugin_identified_candidate(name: str, candidates: list[dict]) -> tuple[str | None, float]:
    """Finds which retrieval candidate a single plugin's evidence pointed to most strongly.

    Args:
        name: Plugin name ("ocr", "color", "barcode").
        candidates: `rerank_debug["candidates"]` list produced by Reranker,
            each carrying `<name>_match_strength` per candidate.

    Returns:
        Tuple of (product_id with the highest match_strength, that
        strength), or (None, 0.0) if no candidate has any positive
        match_strength for this plugin.
    """
    match_key = f"{name}_match_strength"
    best_product_id: str | None = None
    best_strength = 0.0
    for entry in candidates:
        strength = float(entry.get(match_key, 0.0))
        if strength > best_strength:
            best_strength = strength
            best_product_id = entry.get("product_id")
    return best_product_id, best_strength

def greedy_iou_match(
    pred_boxes: list[BoundingBox],
    gt_boxes: list[BoundingBox],
    iou_threshold: float,
    class_ok=None,
) -> tuple[list[tuple[int, int, float]], list[int], list[int]]:
    """Greedily matches predicted boxes to ground-truth boxes by descending IoU.

    Chosen over Hungarian (optimal global) matching for v0.1.0 simplicity
    (project owner decision); reserved for a future version.

    Args:
        pred_boxes: Predicted bounding boxes.
        gt_boxes: Ground-truth bounding boxes.
        iou_threshold: Minimum IoU for a candidate pair to be eligible.
        class_ok: Optional predicate `(pred_index, gt_index) -> bool`;
            when provided, a pair is only eligible if it also satisfies
            this (used for End-to-End's product_id equality requirement).

    Returns:
        Tuple of (matches, unmatched_pred_indices, unmatched_gt_indices),
        where matches is a list of (pred_index, gt_index, iou).
    """
    candidates: list[tuple[float, int, int]] = []
    for pred_index, pred_box in enumerate(pred_boxes):
        for gt_index, gt_box in enumerate(gt_boxes):
            if class_ok is not None and not class_ok(pred_index, gt_index):
                continue
            iou = pred_box.iou(gt_box)
            if iou >= iou_threshold:
                candidates.append((iou, pred_index, gt_index))

    candidates.sort(key=lambda item: item[0], reverse=True)

    used_pred: set[int] = set()
    used_gt: set[int] = set()
    matches: list[tuple[int, int, float]] = []

    for iou, pred_index, gt_index in candidates:
        if pred_index in used_pred or gt_index in used_gt:
            continue
        used_pred.add(pred_index)
        used_gt.add(gt_index)
        matches.append((pred_index, gt_index, iou))

    unmatched_pred = [i for i in range(len(pred_boxes)) if i not in used_pred]
    unmatched_gt = [j for j in range(len(gt_boxes)) if j not in used_gt]
    return matches, unmatched_pred, unmatched_gt


def _safe_divide(numerator: float, denominator: float) -> float:
    """Divides two numbers, returning 0.0 instead of raising on zero division."""
    if denominator == 0:
        return 0.0
    return numerator / denominator


def _mean(values: list[float]) -> float:
    """Returns the arithmetic mean of a list, or 0.0 if empty."""
    return _safe_divide(sum(values), len(values))


def _metric_or_na(stage_report, key: str):
    """Safely extracts a metric from a stage report that might be skipped/dict."""
    if isinstance(stage_report, dict):
        return stage_report.get(key, "N/A")
    return "N/A"
