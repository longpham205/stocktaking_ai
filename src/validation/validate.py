"""Validation mode execution runner (VAL).

Responsibility: load COCO-style ground truth, run the exact same
`InventoryPipeline` used in production (via `run_with_trace` so
intermediate stage results are available for staged VAL metrics — see
02_MODULE_SPECIFICATION.md, Section 9.1 and VAL spec Section 15), and
forward everything to `Evaluator`. Also produces the human-facing report
artifacts: JSON/CSV/summary, a flat `records.csv` for notebooks, a
per-stage bar chart PNG, and color-coded annotated benchmark images.

Expected benchmark layout:

    <benchmark_dir>/
    ├── images/
    │   ├── *.jpg
    │   └── ...
    └── _annotations.coco.json

Ground truth `category_id` is the same stable internal product_id used
everywhere else in this codebase (see src/catalog/metadata.py) — per
project convention, COCO categories are authored with `category_id`
equal to the numeric product_id from `config.catalog.id_mapping` /
`product_ids.json`. No separate product-ID system is created for VAL
(VAL spec, Section 1).
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import cv2

from src.core.config import AppConfig
from src.core.logger import get_logger
from src.core.utils import ensure_dir, generate_id, load_image_bgr
from src.models.models import BoundingBox, ImageData
from src.pipeline.pipeline import InventoryPipeline
from src.validation.evaluator import (
    Evaluator,
    GroundTruthObject,
    ImageEvalInput,
    greedy_iou_match,
)

logger = get_logger(__name__)

_COLOR_TP = (0, 200, 0)  # green (BGR)
_COLOR_FP = (0, 0, 220)  # red
_COLOR_FN = (0, 140, 255)  # orange


class ValidationRunner:
    """Executes Validation Mode: COCO benchmark -> real pipeline -> VAL report."""

    def __init__(self, config: AppConfig) -> None:
        """Initializes the ValidationRunner, its pipeline, and evaluator once.

        Args:
            config: Fully validated application configuration.
        """
        self._config = config
        self._pipeline = InventoryPipeline(config)
        self._evaluator = Evaluator(config)
        self._output_dir = config.resolve_path(config.paths.output_dir)
        self._output_dir.mkdir(parents=True, exist_ok=True)
        logger.info("ValidationRunner initialized.")

    def run(self, benchmark_dir: str) -> dict:
        """Runs validation over a COCO-annotated benchmark directory.

        Args:
            benchmark_dir: Root directory containing `images/` and
                `_annotations.coco.json`.

        Returns:
            The full VAL report dictionary produced by the Evaluator.

        Raises:
            FileNotFoundError: If `_annotations.coco.json` is missing.
        """
        benchmark_root = Path(benchmark_dir)
        images_dir = benchmark_root / "images"
        annotations_path = benchmark_root / "_annotations.coco.json"

        if not annotations_path.is_file():
            raise FileNotFoundError(
                f"COCO annotations file not found: '{annotations_path}'. "
                "VAL requires <benchmark_dir>/_annotations.coco.json (see VAL spec, Section 1)."
            )

        coco_images, ground_truth_by_image_id = self._load_coco_annotations(annotations_path)

        records: list[ImageEvalInput] = []
        for coco_image in coco_images:
            image_path = images_dir / coco_image["file_name"]
            if not image_path.is_file():
                logger.warning("Benchmark image listed in COCO json not found on disk: '%s'", image_path)
                continue

            image_array = load_image_bgr(image_path)
            height, width = image_array.shape[:2]
            image_data = ImageData(
                image_id=generate_id(prefix="val_"),
                source_path=str(image_path),
                image_array=image_array,
                width=width,
                height=height,
            )

            logger.info("Running validation on '%s'", image_path)
            result, trace = self._pipeline.run_with_trace(image_data)

            records.append(
                ImageEvalInput(
                    image_key=image_path.stem,
                    source_path=str(image_path),
                    result=result,
                    trace=trace,
                    ground_truth=ground_truth_by_image_id.get(coco_image["id"], []),
                )
            )

        if not records:
            logger.warning("No benchmark images were evaluated in '%s'", images_dir)

        report = self._evaluator.evaluate(records)
        self._write_reports(report)

        if self._config.validation.save_annotated_images:
            self._save_annotated_images(records)

        self._save_chart(report)
        return report

    # =========================================================================
    # COCO loading
    # =========================================================================

    @staticmethod
    def _load_coco_annotations(
        annotations_path: Path,
    ) -> tuple[list[dict], dict[int, list[GroundTruthObject]]]:
        """Loads and indexes a COCO-style annotations file.

        Args:
            annotations_path: Path to `_annotations.coco.json`.

        Returns:
            Tuple of:
                - `images`: the COCO `images[]` list, unchanged.
                - `ground_truth_by_image_id`: mapping of COCO image id to
                  its list of GroundTruthObject instances.

        Raises:
            ValueError: If the file is not valid COCO JSON.
        """
        with annotations_path.open("r", encoding="utf-8") as file_handle:
            try:
                data = json.load(file_handle)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid COCO JSON: '{annotations_path}'") from exc

        images = data.get("images", [])
        annotations = data.get("annotations", [])

        ground_truth_by_image_id: dict[int, list[GroundTruthObject]] = {}
        for annotation in annotations:
            image_id = annotation["image_id"]
            x, y, w, h = annotation["bbox"]
            gt_object = GroundTruthObject(
                product_id=str(annotation["category_id"]),
                bbox=BoundingBox(x1=x, y1=y, x2=x + w, y2=y + h),
                iscrowd=bool(annotation.get("iscrowd", 0)),
            )
            ground_truth_by_image_id.setdefault(image_id, []).append(gt_object)

        logger.info(
            "Loaded COCO annotations: %d image(s), %d annotation(s) from '%s'",
            len(images),
            len(annotations),
            annotations_path,
        )
        return images, ground_truth_by_image_id

    # =========================================================================
    # Report writing
    # =========================================================================

    def _write_reports(self, report: dict) -> None:
        """Writes the VAL report as JSON, per-image CSV, records.csv, and a text summary.

        Args:
            report: The report dictionary produced by the Evaluator.
        """
        validation_config = self._config.validation

        json_path = self._output_dir / validation_config.report_json_filename
        with json_path.open("w", encoding="utf-8") as file_handle:
            json.dump(report, file_handle, indent=2, default=str, ensure_ascii=False)
        logger.info("Saved VAL report JSON to '%s'", json_path)

        csv_path = self._output_dir / validation_config.report_csv_filename
        per_image = report.get("per_image", [])
        if per_image:
            with csv_path.open("w", encoding="utf-8", newline="") as file_handle:
                writer = csv.DictWriter(file_handle, fieldnames=list(per_image[0].keys()))
                writer.writeheader()
                writer.writerows(per_image)
            logger.info("Saved VAL per-image report CSV to '%s'", csv_path)

        if validation_config.export_records:
            records_rows = report.get("records", [])
            records_path = self._output_dir / validation_config.records_filename
            if records_rows:
                with records_path.open("w", encoding="utf-8", newline="") as file_handle:
                    writer = csv.DictWriter(file_handle, fieldnames=list(records_rows[0].keys()))
                    writer.writeheader()
                    writer.writerows(records_rows)
                logger.info("Saved flat per-crop records to '%s'", records_path)

        self._write_summary(report)

    def _write_summary(self, report: dict) -> None:
        """Writes a human-readable text summary covering every enabled stage.

        Args:
            report: The report dictionary produced by the Evaluator.
        """
        lines = ["STOCKTAKING AI - VALIDATION SUMMARY", "=" * 60]
        lines.append(f"Total images evaluated : {report['dataset']['total_images']}")
        lines.append(f"IoU match threshold     : {report['configuration']['iou_match_threshold']}")
        lines.append(f"Top-K (retrieval eval)  : {report['configuration']['top_k']}")
        lines.append("-" * 60)

        for stage_name in (
            "detection", "cropping", "overlap", "segmentation",
            "retrieval", "decision", "plugins", "fusion", "end_to_end",
        ):
            stage_report = report.get(stage_name)
            lines.append(f"[{stage_name.upper()}]")
            if stage_report == "skipped_by_config":
                lines.append("  skipped_by_config")
            elif stage_name == "plugins" and isinstance(stage_report, dict):
                for plugin_name, plugin_stats in stage_report.items():
                    lines.append(f"  {plugin_name}: {_format_kv(plugin_stats)}")
            elif isinstance(stage_report, dict):
                lines.append(f"  {_format_kv(stage_report)}")
            else:
                lines.append(f"  {stage_report}")
            lines.append("-" * 60)

        lines.append("[LATENCY BREAKDOWN, ms]")
        for stage, values in report.get("latency", {}).items():
            lines.append(f"  {stage}: mean={values['mean']} min={values['min']} max={values['max']}")

        summary_path = self._output_dir / self._config.validation.summary_filename
        summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        logger.info("Saved VAL summary to '%s'", summary_path)

    # =========================================================================
    # Annotated images (green=TP, red=FP, orange dashed=FN)
    # =========================================================================

    def _save_annotated_images(self, records: list[ImageEvalInput]) -> None:
        """Saves one color-coded annotated image per benchmark image.

        Green solid = correct prediction (TP). Red solid = wrong
        prediction (FP — bbox matched a GT but wrong product_id, or no
        GT matched at all). Orange dashed = ground-truth object with no
        matching prediction (FN, fully missed).

        Args:
            records: Evaluated benchmark images (with pipeline results).
        """
        images_out_dir = self._output_dir / self._config.validation.annotated_images_dirname
        ensure_dir(images_out_dir)
        iou_threshold = self._config.validation.iou_match_threshold

        for record in records:
            image = load_image_bgr(record.source_path).copy()
            non_crowd_gt = [gt for gt in record.ground_truth if not gt.iscrowd]

            pred_boxes = [item.bbox for item in record.result.items]
            pred_ids = [item.product_id for item in record.result.items]
            gt_boxes = [gt.bbox for gt in non_crowd_gt]
            gt_ids = [gt.product_id for gt in non_crowd_gt]

            def class_ok(pred_index: int, gt_index: int, _p=pred_ids, _g=gt_ids) -> bool:
                return _p[pred_index] == _g[gt_index]

            matches, unmatched_pred, unmatched_gt = greedy_iou_match(
                pred_boxes, gt_boxes, iou_threshold, class_ok=class_ok if pred_ids and gt_ids else None
            )

            for pred_index, _gt_index, _iou in matches:
                self._draw_box(image, pred_boxes[pred_index], _COLOR_TP, f"{pred_ids[pred_index]} OK", dashed=False)
            for pred_index in unmatched_pred:
                self._draw_box(image, pred_boxes[pred_index], _COLOR_FP, f"{pred_ids[pred_index]} WRONG", dashed=False)
            for gt_index in unmatched_gt:
                self._draw_box(image, gt_boxes[gt_index], _COLOR_FN, f"{gt_ids[gt_index]} MISSED", dashed=True)

            out_path = images_out_dir / f"{record.image_key}.jpg"
            cv2.imwrite(str(out_path), image)

        logger.info("Saved %d annotated validation image(s) to '%s'", len(records), images_out_dir)

    @staticmethod
    def _draw_box(image, bbox: BoundingBox, color: tuple[int, int, int], label: str, dashed: bool) -> None:
        """Draws a single labeled bounding box (solid or dashed) onto an image.

        Args:
            image: BGR image array, modified in place.
            bbox: Box to draw.
            color: BGR color.
            label: Text label drawn above the box.
            dashed: Whether to draw a dashed rectangle (used for FN/missed).
        """
        x1, y1, x2, y2 = int(bbox.x1), int(bbox.y1), int(bbox.x2), int(bbox.y2)
        if dashed:
            _draw_dashed_rectangle(image, (x1, y1), (x2, y2), color, thickness=2, dash_length=10)
        else:
            cv2.rectangle(image, (x1, y1), (x2, y2), color, 2)
        cv2.putText(image, label, (x1, max(15, y1 - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2, cv2.LINE_AA)

    # =========================================================================
    # Summary chart
    # =========================================================================

    def _save_chart(self, report: dict) -> None:
        """Saves a bar chart PNG (precision/recall/f1 per enabled stage).

        Args:
            report: The report dictionary produced by the Evaluator.
        """
        try:
            import matplotlib

            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
        except ImportError:
            logger.warning("matplotlib not installed; skipping validation summary chart.")
            return

        stage_names = ["detection", "overlap", "decision", "fusion", "end_to_end"]
        precisions, recalls, f1s, labels = [], [], [], []
        for stage_name in stage_names:
            stage_report = report.get(stage_name)
            if not isinstance(stage_report, dict):
                continue
            has_all_three = all(
                isinstance(stage_report.get(key), (int, float)) for key in ("precision", "recall", "f1")
            )
            if not has_all_three:
                continue  # e.g. fusion uses accuracy_before/after instead — don't plot a misleading 0
            labels.append(stage_name)
            precisions.append(stage_report["precision"])
            recalls.append(stage_report["recall"])
            f1s.append(stage_report["f1"])

        if not labels:
            return

        x = range(len(labels))
        width = 0.25
        fig, ax = plt.subplots(figsize=(max(6, len(labels) * 1.8), 4.5))
        ax.bar([i - width for i in x], precisions, width, label="Precision")
        ax.bar(list(x), recalls, width, label="Recall")
        ax.bar([i + width for i in x], f1s, width, label="F1")
        ax.set_xticks(list(x))
        ax.set_xticklabels(labels, rotation=20)
        ax.set_ylim(0, 1.05)
        ax.set_title("Stocktaking AI - Validation Metrics by Stage")
        ax.legend()
        fig.tight_layout()

        chart_path = self._output_dir / self._config.validation.chart_filename
        fig.savefig(chart_path, dpi=120)
        plt.close(fig)
        logger.info("Saved validation summary chart to '%s'", chart_path)


def _draw_dashed_rectangle(image, pt1, pt2, color, thickness: int = 2, dash_length: int = 10) -> None:
    """Draws a dashed rectangle outline (cv2 has no built-in dashed style).

    Args:
        image: BGR image array, modified in place.
        pt1: Top-left corner (x, y).
        pt2: Bottom-right corner (x, y).
        color: BGR color.
        thickness: Line thickness.
        dash_length: Length of each dash segment, in pixels.
    """
    x1, y1 = pt1
    x2, y2 = pt2
    for (start, end) in [((x1, y1), (x2, y1)), ((x1, y2), (x2, y2))]:
        _draw_dashed_line(image, start, end, color, thickness, dash_length)
    for (start, end) in [((x1, y1), (x1, y2)), ((x2, y1), (x2, y2))]:
        _draw_dashed_line(image, start, end, color, thickness, dash_length)


def _draw_dashed_line(image, pt1, pt2, color, thickness: int, dash_length: int) -> None:
    """Draws a single dashed line segment."""
    x1, y1 = pt1
    x2, y2 = pt2
    length = max(1, int(((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5))
    dashes = max(1, length // (dash_length * 2))
    for i in range(dashes):
        start_ratio = (i * 2) / (dashes * 2)
        end_ratio = (i * 2 + 1) / (dashes * 2)
        start = (int(x1 + (x2 - x1) * start_ratio), int(y1 + (y2 - y1) * start_ratio))
        end = (int(x1 + (x2 - x1) * end_ratio), int(y1 + (y2 - y1) * end_ratio))
        cv2.line(image, start, end, color, thickness)


def _format_kv(data: dict) -> str:
    """Formats a flat dict as a compact, single-line 'k=v k=v ...' string.

    Args:
        data: A stage's metric dict.

    Returns:
        A compact string, skipping large nested containers.
    """
    parts = []
    for key, value in data.items():
        if isinstance(value, (dict, list)):
            continue
        parts.append(f"{key}={value}")
    return " ".join(parts)
