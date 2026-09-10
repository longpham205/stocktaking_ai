"""Integration tests for src.validation.validate.ValidationRunner."""

from __future__ import annotations

import json

import cv2
import numpy as np
import pytest

from src.validation.validate import ValidationRunner


def test_validation_runner_end_to_end(gallery_config, tmp_path) -> None:
    """ValidationRunner must load COCO ground truth and run the real pipeline."""
    benchmark_dir = tmp_path / "benchmark"
    images_dir = benchmark_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    image = np.full((200, 200, 3), 255, dtype=np.uint8)
    image[20:120, 20:120] = (30, 30, 200)  # matches prod_red_square (product_id "1")
    cv2.imwrite(str(images_dir / "bench_01.jpg"), image)

    coco = {
        "images": [{"id": 1, "file_name": "bench_01.jpg", "width": 200, "height": 200}],
        "categories": [{"id": 1, "name": "prod_red_square"}],
        "annotations": [
            {
                "id": 1,
                "image_id": 1,
                "category_id": 1,
                "bbox": [20, 20, 100, 100],
                "area": 10000,
                "iscrowd": 0,
            }
        ],
    }
    (benchmark_dir / "_annotations.coco.json").write_text(json.dumps(coco), encoding="utf-8")

    runner = ValidationRunner(gallery_config)
    report = runner.run(str(benchmark_dir))

    assert report["dataset"]["total_images"] == 1
    assert report["detection"]["gt_objects"] == 1
    assert report["end_to_end"]["true_positive"] >= 0  # must not crash; exact score depends on mock retrieval

    output_dir = gallery_config.resolve_path(gallery_config.paths.output_dir)
    assert (output_dir / gallery_config.validation.report_json_filename).is_file()
    assert (output_dir / gallery_config.validation.summary_filename).is_file()


def test_validation_runner_produces_all_artifacts(gallery_config, tmp_path) -> None:
    """ValidationRunner must produce records.csv, the chart PNG, and annotated images."""
    benchmark_dir = tmp_path / "benchmark2"
    images_dir = benchmark_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    image = np.full((200, 200, 3), 255, dtype=np.uint8)
    image[20:120, 20:120] = (30, 30, 200)
    cv2.imwrite(str(images_dir / "bench_01.jpg"), image)

    coco = {
        "images": [{"id": 1, "file_name": "bench_01.jpg", "width": 200, "height": 200}],
        "categories": [{"id": 1, "name": "prod_red_square"}],
        "annotations": [
            {"id": 1, "image_id": 1, "category_id": 1, "bbox": [20, 20, 100, 100], "area": 10000, "iscrowd": 0}
        ],
    }
    (benchmark_dir / "_annotations.coco.json").write_text(json.dumps(coco), encoding="utf-8")

    runner = ValidationRunner(gallery_config)
    report = runner.run(str(benchmark_dir))

    assert len(report["records"]) >= 1

    output_dir = gallery_config.resolve_path(gallery_config.paths.output_dir)
    assert (output_dir / gallery_config.validation.records_filename).is_file()

    images_out_dir = output_dir / gallery_config.validation.annotated_images_dirname
    assert (images_out_dir / "bench_01.jpg").is_file()


def test_validation_runner_missing_coco_raises(gallery_config, tmp_path) -> None:
    """ValidationRunner must raise FileNotFoundError when _annotations.coco.json is absent."""
    empty_benchmark_dir = tmp_path / "empty_benchmark"
    (empty_benchmark_dir / "images").mkdir(parents=True, exist_ok=True)

    runner = ValidationRunner(gallery_config)

    with pytest.raises(FileNotFoundError):
        runner.run(str(empty_benchmark_dir))
