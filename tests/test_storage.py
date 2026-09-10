"""Unit tests for src.storage.results.StorageManager."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np

from src.models.models import BoundingBox, InventoryItem, InventoryResult
from src.storage.results import StorageManager


def _make_sample_result() -> InventoryResult:
    """Builds a minimal InventoryResult for storage testing."""
    item = InventoryItem(
        product_id="1",
        product_name="Product A",
        bbox=BoundingBox(10, 10, 60, 60),
        detection_confidence=0.8,
        similarity_score=0.9,
        final_confidence=0.85,
        status="accepted",
    )
    return InventoryResult(
        image_id="img_1",
        source_path="mem.jpg",
        items=[item],
        total_items=1,
        processing_time_ms=12.3,
        timestamp="2026-01-01T00:00:00+00:00",
    )


def test_save_json_writes_valid_file(test_config) -> None:
    """save_json must write a well-formed, loadable JSON file."""
    storage = StorageManager(test_config)
    result = _make_sample_result()

    path = storage.save_json(result)

    assert Path(path).is_file()
    with open(path, "r", encoding="utf-8") as file_handle:
        payload = json.load(file_handle)
    assert payload["image_id"] == "img_1"
    assert payload["items"][0]["product_id"] == "1"


def test_save_csv_writes_expected_rows(test_config) -> None:
    """save_csv must write one row per inventory item with expected fields."""
    storage = StorageManager(test_config)
    result = _make_sample_result()

    path = storage.save_csv(result)

    with open(path, "r", encoding="utf-8", newline="") as file_handle:
        rows = list(csv.DictReader(file_handle))
    assert len(rows) == 1
    assert rows[0]["product_id"] == "1"
    assert rows[0]["status"] == "accepted"


def test_save_annotated_image_writes_file(test_config) -> None:
    """save_annotated_image must write a valid, non-empty image file."""
    storage = StorageManager(test_config)
    result = _make_sample_result()
    image = np.full((100, 100, 3), 255, dtype=np.uint8)

    path = storage.save_annotated_image(image, result)

    assert Path(path).is_file()
    assert Path(path).stat().st_size > 0


def test_save_all_respects_toggles(test_config) -> None:
    """save_all must skip artifacts whose configuration toggle is disabled."""
    csv_disabled = test_config.storage.model_copy(update={"save_csv": False})
    updated_config = test_config.model_copy(update={"storage": csv_disabled})
    storage = StorageManager(updated_config)
    result = _make_sample_result()
    image = np.full((100, 100, 3), 255, dtype=np.uint8)

    saved = storage.save_all(image, result)

    assert "csv" not in saved
    assert "json" in saved
    assert "annotated_image" in saved
