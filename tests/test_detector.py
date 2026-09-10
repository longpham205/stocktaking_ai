"""Unit tests for src.detection.detector.Detector."""

from __future__ import annotations

import numpy as np

from src.detection.detector import Detector
from src.models.models import ImageData


def test_detector_finds_single_region(test_config, synthetic_image: np.ndarray) -> None:
    """Detector should find exactly one region in a single-square image."""
    detector = Detector(test_config)
    image_data = ImageData(image_id="t1", source_path="mem", image_array=synthetic_image, width=300, height=300)

    result = detector.detect(image_data)

    assert result.image_id == "t1"
    assert len(result.detections) == 1
    assert result.processing_time_ms >= 0.0


def test_detector_finds_two_regions(test_config, synthetic_multi_image: np.ndarray) -> None:
    """Detector should find two separate regions in a two-square image."""
    detector = Detector(test_config)
    image_data = ImageData(
        image_id="t2", source_path="mem", image_array=synthetic_multi_image, width=300, height=300
    )

    result = detector.detect(image_data)

    assert len(result.detections) == 2


def test_detector_confidence_in_valid_range(test_config, synthetic_image: np.ndarray) -> None:
    """All detection confidence scores must fall within [0.0, 1.0]."""
    detector = Detector(test_config)
    image_data = ImageData(image_id="t3", source_path="mem", image_array=synthetic_image, width=300, height=300)

    result = detector.detect(image_data)

    for detection in result.detections:
        assert 0.0 <= detection.confidence <= 1.0


def test_detector_blank_image_finds_nothing(test_config) -> None:
    """A perfectly blank image should produce zero detections."""
    detector = Detector(test_config)
    blank = np.full((200, 200, 3), 255, dtype=np.uint8)
    image_data = ImageData(image_id="blank", source_path="mem", image_array=blank, width=200, height=200)

    result = detector.detect(image_data)

    assert result.detections == []


def test_detector_rejects_unknown_backend(test_config) -> None:
    """Detector must raise ValueError for a backend with no registered loader."""
    bad_config = test_config.detection.model_copy(update={"backend": "not_a_real_backend"})
    config = test_config.model_copy(update={"detection": bad_config})

    import pytest

    with pytest.raises(ValueError, match="Unsupported detection backend"):
        Detector(config)
