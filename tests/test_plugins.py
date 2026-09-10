"""Unit tests for src.plugins.* (ocr, color, barcode, manager)."""

from __future__ import annotations

import cv2
import numpy as np

from src.core.utils import generate_id
from src.models.models import BoundingBox, CropImage, DecisionResult
from src.plugins.barcode import BarcodePlugin
from src.plugins.color import ColorPlugin
from src.plugins.manager import PluginManager
from src.plugins.ocr import OcrPlugin


def _make_crop(raw_image_array: np.ndarray) -> CropImage:
    """Build a CropImage with separate retrieval and raw-resolution images.

    Plugins should consume ``raw_image_array`` so their tests reflect the
    production contract where OCR/Color/Barcode preserve source resolution.
    """
    height, width = raw_image_array.shape[:2]

    # Simulate the fixed-size representation used by Retrieval.
    image_array = cv2.resize(
        raw_image_array,
        (224, 224),
        interpolation=cv2.INTER_AREA,
    )

    return CropImage(
        crop_id=generate_id(),
        image_id="img",
        image_array=image_array,
        raw_image_array=raw_image_array,
        source_bbox=BoundingBox(0, 0, width, height),
        detection_confidence=0.5,
        detection_index=0,
        used_refined_bbox=False,
    )


def _decision(
    needs_plugin: bool,
    trigger_reasons: frozenset[str],
    forced_plugins: frozenset[str] = frozenset(),
) -> DecisionResult:
    """Build a minimal DecisionResult for plugin manager testing."""
    return DecisionResult(
        crop_id="crop_1",
        product_id="prod_a",
        product_name="Product A",
        detection_confidence=0.5,
        similarity_score=0.5,
        final_confidence=0.5,
        status="uncertain",
        needs_plugin=needs_plugin,
        trigger_reasons=trigger_reasons,
        forced_plugins=forced_plugins,
    )


def test_color_plugin_extracts_palette(test_config) -> None:
    """ColorPlugin should extract a non-empty dominant color palette."""
    plugin = ColorPlugin(test_config)

    raw_image = np.full(
        (80, 80, 3),
        (30, 30, 200),
        dtype=np.uint8,
    )

    output = plugin.run(_make_crop(raw_image))

    assert len(output["palette"]) >= 1
    assert output["palette"][0].startswith("#")


def test_ocr_plugin_returns_expected_keys(test_config) -> None:
    """OcrPlugin.run must always return the documented dictionary keys."""
    plugin = OcrPlugin(test_config)

    raw_image = np.full(
        (80, 80, 3),
        255,
        dtype=np.uint8,
    )

    output = plugin.run(_make_crop(raw_image))

    expected_keys = {
        "text",
        "text_length",
        "confidence",
        "max_fragment_confidence",
        "mean_fragment_confidence",
        "fragments",
        "rotation",
        "score",
        "information_score",
        "useful_fragment_count",
        "useful_text_length",
        "image_shape",
        "ocr_boxes",
        "orientation_scores",
        "orientation_candidates",
        "orientation_ambiguous",
        "orientation_candidate_count",
        "latency_ms",
    }

    assert expected_keys.issubset(output.keys())


def test_barcode_plugin_no_barcode_present(test_config) -> None:
    """BarcodePlugin must return no barcode and zero confidence on a blank crop."""
    plugin = BarcodePlugin(test_config)

    raw_image = np.full(
        (80, 80, 3),
        255,
        dtype=np.uint8,
    )

    output = plugin.run(_make_crop(raw_image))

    assert output["barcodes"] == []
    assert output["confidence"] == 0.0
    assert output["preprocessing_stage"] in {
        "no_barcode_region",
        "none",
    }
    assert "latency_ms" in output


def test_plugin_manager_skips_when_not_needed(test_config) -> None:
    """PluginManager must not execute any plugin when needs_plugin is False."""
    manager = PluginManager(test_config)

    crop = _make_crop(
        np.full(
            (80, 80, 3),
            255,
            dtype=np.uint8,
        )
    )

    decision = _decision(
        needs_plugin=False,
        trigger_reasons=frozenset(),
    )

    result = manager.run_plugins(crop, decision)

    assert result.executed_plugins == []


def test_plugin_manager_runs_all_when_uncertain(test_config) -> None:
    """When trigger_reasons includes 'uncertain', every enabled plugin must run."""
    manager = PluginManager(test_config)

    crop = _make_crop(
        np.full(
            (80, 80, 3),
            (30, 30, 200),
            dtype=np.uint8,
        )
    )

    decision = _decision(
        needs_plugin=True,
        trigger_reasons=frozenset({"uncertain"}),
    )

    result = manager.run_plugins(crop, decision)

    assert set(result.executed_plugins) == {
        "ocr",
        "color",
        "barcode",
    }


def test_plugin_manager_runs_all_when_ambiguous(test_config) -> None:
    """When trigger_reasons includes 'ambiguous', every enabled plugin must run."""
    manager = PluginManager(test_config)

    crop = _make_crop(
        np.full(
            (80, 80, 3),
            (30, 30, 200),
            dtype=np.uint8,
        )
    )

    decision = _decision(
        needs_plugin=True,
        trigger_reasons=frozenset({"ambiguous"}),
    )

    result = manager.run_plugins(crop, decision)

    assert set(result.executed_plugins) == {
        "ocr",
        "color",
        "barcode",
    }


def test_plugin_manager_runs_only_forced_when_force_only(test_config) -> None:
    """When trigger_reasons is exactly {'force'}, only forced_plugins must run."""
    manager = PluginManager(test_config)

    crop = _make_crop(
        np.full(
            (80, 80, 3),
            (30, 30, 200),
            dtype=np.uint8,
        )
    )

    decision = _decision(
        needs_plugin=True,
        trigger_reasons=frozenset({"force"}),
        forced_plugins=frozenset({"barcode"}),
    )

    result = manager.run_plugins(crop, decision)

    assert result.executed_plugins == ["barcode"]


def test_plugin_manager_force_cannot_override_disabled_plugin(test_config) -> None:
    """A plugin's own `enabled: false` must win even if force_rules requests it."""
    disabled_barcode = test_config.plugins.barcode.model_copy(
        update={"enabled": False}
    )

    disabled_plugins_config = test_config.plugins.model_copy(
        update={"barcode": disabled_barcode}
    )

    config = test_config.model_copy(
        update={"plugins": disabled_plugins_config}
    )

    manager = PluginManager(config)

    crop = _make_crop(
        np.full(
            (80, 80, 3),
            255,
            dtype=np.uint8,
        )
    )

    decision = _decision(
        needs_plugin=True,
        trigger_reasons=frozenset({"force"}),
        forced_plugins=frozenset({"barcode"}),
    )

    result = manager.run_plugins(crop, decision)

    assert result.executed_plugins == []


def test_plugin_manager_respects_global_disable(test_config) -> None:
    """PluginManager must skip execution entirely when subsystem is disabled."""
    disabled_plugins = test_config.plugins.model_copy(
        update={"enabled": False}
    )

    disabled_config = test_config.model_copy(
        update={"plugins": disabled_plugins}
    )

    manager = PluginManager(disabled_config)

    crop = _make_crop(
        np.full(
            (80, 80, 3),
            255,
            dtype=np.uint8,
        )
    )

    decision = _decision(
        needs_plugin=True,
        trigger_reasons=frozenset({"uncertain"}),
    )

    result = manager.run_plugins(crop, decision)

    assert result.executed_plugins == []
    
def test_make_crop_preserves_raw_resolution() -> None:
    """CropImage must keep raw resolution separate from retrieval resolution."""
    raw_image = np.zeros((480, 320, 3), dtype=np.uint8)

    crop = _make_crop(raw_image)

    assert crop.raw_image_array.shape == (480, 320, 3)
    assert crop.image_array.shape == (224, 224, 3)
    assert crop.source_bbox == BoundingBox(0, 0, 320, 480)