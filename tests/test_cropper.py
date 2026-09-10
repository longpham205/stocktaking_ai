"""Unit tests for src.detection.cropper.Cropper."""

from __future__ import annotations

import numpy as np

from src.detection.cropper import Cropper
from src.models.models import (
    BoundingBox,
    Detection,
    DetectionResult,
    ImageData,
    RefinedBox,
    RefinementResult,
)


def _empty_refinement(image_id: str) -> RefinementResult:
    """Builds an untriggered RefinementResult for tests that don't need refinement."""
    return RefinementResult(image_id=image_id, triggered=False, backend="none")


def test_cropper_produces_target_size_crops(test_config, synthetic_image: np.ndarray) -> None:
    """Every crop must be resized to the configured target_size."""
    cropper = Cropper(test_config)
    image_data = ImageData(image_id="t1", source_path="mem", image_array=synthetic_image, width=300, height=300)
    detection_result = DetectionResult(
        image_id="t1",
        detections=[Detection(bbox=BoundingBox(50, 50, 150, 150), confidence=0.9)],
    )

    crops = cropper.crop(image_data, detection_result, _empty_refinement("t1"))

    assert len(crops) == 1
    expected_w, expected_h = test_config.cropping.target_size
    assert crops[0].image_array.shape[1] == expected_w
    assert crops[0].image_array.shape[0] == expected_h
    assert crops[0].used_refined_bbox is False
    assert crops[0].detection_index == 0


def test_cropper_clips_out_of_bounds_boxes(test_config, synthetic_image: np.ndarray) -> None:
    """Boxes that extend beyond image boundaries must be clipped, not crash."""
    cropper = Cropper(test_config)
    image_data = ImageData(image_id="t2", source_path="mem", image_array=synthetic_image, width=300, height=300)
    detection_result = DetectionResult(
        image_id="t2",
        detections=[Detection(bbox=BoundingBox(-50, -50, 350, 350), confidence=0.9)],
    )

    crops = cropper.crop(image_data, detection_result, _empty_refinement("t2"))

    assert len(crops) == 1


def test_cropper_skips_zero_area_detection(test_config, synthetic_image: np.ndarray) -> None:
    """A degenerate zero-area bounding box must be silently skipped."""
    cropper = Cropper(test_config)
    image_data = ImageData(image_id="t3", source_path="mem", image_array=synthetic_image, width=300, height=300)
    detection_result = DetectionResult(
        image_id="t3",
        detections=[Detection(bbox=BoundingBox(500, 500, 500, 500), confidence=0.9)],
    )

    crops = cropper.crop(image_data, detection_result, _empty_refinement("t3"))

    assert crops == []


def test_cropper_preserves_source_bbox_and_confidence(test_config, synthetic_image: np.ndarray) -> None:
    """The cropper must not mutate the original detection's bbox or confidence."""
    cropper = Cropper(test_config)
    image_data = ImageData(image_id="t4", source_path="mem", image_array=synthetic_image, width=300, height=300)
    original_bbox = BoundingBox(50, 50, 150, 150)
    detection_result = DetectionResult(
        image_id="t4",
        detections=[Detection(bbox=original_bbox, confidence=0.77)],
    )

    crops = cropper.crop(image_data, detection_result, _empty_refinement("t4"))

    assert crops[0].source_bbox is original_bbox
    assert crops[0].detection_confidence == 0.77
    assert original_bbox.x1 == 50  # never mutated


def test_cropper_uses_refined_bbox_when_available(test_config, synthetic_image: np.ndarray) -> None:
    """Cropper must crop from the refined bbox when one is present and enabled."""
    cropper = Cropper(test_config)
    image_data = ImageData(image_id="t5", source_path="mem", image_array=synthetic_image, width=300, height=300)
    original_bbox = BoundingBox(0, 0, 10, 10)  # would produce a tiny crop
    detection_result = DetectionResult(
        image_id="t5",
        detections=[Detection(bbox=original_bbox, confidence=0.9)],
    )
    refined_bbox = BoundingBox(50, 50, 150, 150)  # the "real" product region
    refinement = RefinementResult(
        image_id="t5",
        triggered=True,
        backend="mock_refiner",
        refined_boxes=[
            RefinedBox(
                detection_index=0,
                refined_bbox=refined_bbox,
                mask_area_ratio=0.9,
                refinement_confidence=0.8,
                backend="mock_refiner",
                used_fallback=False,
            )
        ],
    )

    crops = cropper.crop(image_data, detection_result, refinement)

    assert crops[0].used_refined_bbox is True
    assert crops[0].source_bbox.x1 == 50  # took the refined box, not the original
    assert original_bbox.x1 == 0  # original Detection bbox untouched


def test_cropper_ignores_fallback_refinement(test_config, synthetic_image: np.ndarray) -> None:
    """A RefinedBox with used_fallback=True must NOT be treated as refined."""
    cropper = Cropper(test_config)
    image_data = ImageData(image_id="t6", source_path="mem", image_array=synthetic_image, width=300, height=300)
    original_bbox = BoundingBox(50, 50, 150, 150)
    detection_result = DetectionResult(image_id="t6", detections=[Detection(bbox=original_bbox, confidence=0.9)])
    refinement = RefinementResult(
        image_id="t6",
        triggered=True,
        backend="mock_refiner",
        refined_boxes=[
            RefinedBox(
                detection_index=0,
                refined_bbox=original_bbox,
                mask_area_ratio=0.0,
                refinement_confidence=0.0,
                backend="mock_refiner",
                used_fallback=True,
            )
        ],
    )

    crops = cropper.crop(image_data, detection_result, refinement)

    assert crops[0].used_refined_bbox is False


def test_cropper_respects_use_refined_bbox_toggle(test_config, synthetic_image: np.ndarray) -> None:
    """When cropping.use_refined_bbox is False, refinement must be ignored entirely."""
    disabled_cropping = test_config.cropping.model_copy(update={"use_refined_bbox": False})
    config = test_config.model_copy(update={"cropping": disabled_cropping})
    cropper = Cropper(config)

    image_data = ImageData(image_id="t7", source_path="mem", image_array=synthetic_image, width=300, height=300)
    original_bbox = BoundingBox(50, 50, 150, 150)
    detection_result = DetectionResult(image_id="t7", detections=[Detection(bbox=original_bbox, confidence=0.9)])
    refinement = RefinementResult(
        image_id="t7",
        triggered=True,
        backend="mock_refiner",
        refined_boxes=[
            RefinedBox(
                detection_index=0,
                refined_bbox=BoundingBox(0, 0, 200, 200),
                mask_area_ratio=0.9,
                refinement_confidence=0.9,
                backend="mock_refiner",
                used_fallback=False,
            )
        ],
    )

    crops = cropper.crop(image_data, detection_result, refinement)

    assert crops[0].used_refined_bbox is False
    assert crops[0].source_bbox.x1 == 50
