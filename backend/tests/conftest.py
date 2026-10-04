"""Shared pytest fixtures for isolated, mock-driven module testing.

Per 03_DEVELOPMENT_RULES.md, Rule 24: every module must be testable in
complete isolation using mock classes or dummy inputs. These fixtures
build a throwaway project structure (config + tiny gallery + catalog)
under pytest's `tmp_path`, so tests never depend on or mutate the real
`data/` or `configs/` directories.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import cv2
import numpy as np
import pytest
import yaml

from engine.core.config import AppConfig
from engine.pipeline.build import BuildPipeline


def _skip_without_ml_extra(exc: ImportError) -> None:
    """The light install (`uv sync` without `--extra ml`) has no EasyOCR: tests that build the real
    OCR plugin are skipped there, with the reason, instead of failing. With EasyOCR installed an
    ImportError is a real failure and is re-raised."""
    if "easyocr" in str(exc) and importlib.util.find_spec("easyocr") is None:
        pytest.skip("needs the ml extra: uv sync --extra ml (easyocr is not installed)")
    raise exc


@pytest.hookimpl(wrapper=True)
def pytest_runtest_setup(item):
    try:
        return (yield)
    except ImportError as exc:
        _skip_without_ml_extra(exc)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item):
    try:
        return (yield)
    except ImportError as exc:
        _skip_without_ml_extra(exc)


@pytest.fixture
def synthetic_image() -> np.ndarray:
    """Creates a synthetic BGR image with one clear red rectangular region."""
    image = np.full((300, 300, 3), 255, dtype=np.uint8)
    image[50:150, 50:150] = (30, 30, 200)  # a red "product" square
    return image


@pytest.fixture
def synthetic_multi_image() -> np.ndarray:
    """Creates a synthetic BGR image with two distinct colored regions."""
    image = np.full((300, 300, 3), 255, dtype=np.uint8)
    image[30:130, 30:130] = (30, 30, 200)  # red
    image[170:270, 170:270] = (200, 60, 30)  # blue
    return image


def _write_default_config_yaml(config_path: Path, project_root: Path) -> None:
    """Writes a minimal but complete config.yaml for isolated testing.

    Args:
        config_path: Destination path of the config.yaml file.
        project_root: Root directory the config's relative paths resolve against.
    """
    config_dict = {
        "app": {"name": "Stocktaking AI Test", "version": "0.1.0-test", "environment": "development", "device": "cpu"},
        "logging": {
            "level": "WARNING",
            "log_dir": "data/cache/logs",
            "log_to_file": False,
            "log_to_console": False,
            "max_bytes": 1048576,
            "backup_count": 1,
        },
        "paths": {
            "gallery_dir": "data/gallery",
            "benchmark_dir": "data/benchmark",
            "benchmark_images_dir": "data/benchmark/images",
            "benchmark_labels_dir": "data/benchmark/labels",
            "query_dir": "data/query",
            "output_dir": "data/outputs",
            "cache_dir": "data/cache",
            "metadata_dir": "data/metadata",
            "detector_weights_dir": "weights/detector",
            "retriever_weights_dir": "weights/retriever",
            "plugin_weights_dir": "weights/plugins",
            "refinement_weights_dir": "weights/refinement",
        },
        "catalog": {"source": "sqlite", "db_path": "data/db/app.db"},
        "detection": {
            "backend": "mock_contour",
            "weights_path": "weights/detector/model.pt",
            "confidence_threshold": 0.2,
            "min_box_area_ratio": 0.001,
            "max_box_area_ratio": 0.6,
            "min_aspect_ratio": 0.15,
            "max_aspect_ratio": 6.0,
            "canny_threshold_1": 40,
            "canny_threshold_2": 120,
            "blur_kernel_size": 5,
            "max_detections": 50,
            "rf_detr": {
                "variant": "base",
                "weights_path": "",
                "device": "cpu",
                "inference": {"optimize": True, "compile": False, "batch_size": 1, "dtype": "float32", "inplace": False},
            },
        },
        "refinement": {
            "enabled": True,
            "backend": "none",
            "trigger": {
                "enabled": True,
                "iou_threshold": 0.05,
                "overlap_ratio_threshold": 0.20,
                "min_overlapping_pairs": 1,
                "require_multiple_detections": True,
            },
            "sam2": {
                "model_type": "sam2.1_hiera_small",
                "checkpoint_path": "weights/refinement/sam2/sam2.1_hiera_small.pt",
                "model_config": "",
                "device": "cpu",
                "prompt_type": "box",
                "use_source_image": True,
                "per_detection": True,
                "mask_threshold": 0.5,
                "min_mask_area_ratio": 0.001,
            },
            "output": {
                "use_mask_bbox": True,
                "fallback_to_detection_bbox": True,
                "min_mask_coverage_ratio": 0.20,
                "max_bbox_expansion_ratio": 1.50,
            },
        },
        "cropping": {"padding_pixels": 4, "target_size": [128, 128], "use_refined_bbox": True},
        "retrieval": {
            "backend": "mock_visual_embedding",
            "embedding_dim": 768,
            "gallery_index_path": "data/cache/gallery_index.faiss",
            "gallery_metadata_path": "data/cache/gallery_metadata.json",
            "top_k": 5,
            "color_hist_bins": 8,
            "build_gallery_index": True,
            "siglip2": {"model_name": "google/siglip2-base-patch16-224", "weights_path": "", "device": "cpu"},
        },
        "decision": {
            "similarity_threshold": 0.55,
            "min_confidence_accept": 0.60,
            "uncertain_band": 0.15,
            "detection_weight": 0.35,
            "similarity_weight": 0.65,
            "ambiguous_top_n": 3,
            "ambiguous_margin": 0.05,
        },
        "plugins": {
            "enabled": True,
            "ocr": {"enabled": True, "language": "en", "device": "cpu", "min_text_length": 2, "confidence_boost": 0.08},
            "color": {"enabled": True, "n_clusters": 3, "confidence_boost": 0.03},
            "barcode": {"enabled": True, "confidence_boost": 0.20},
        },
        "storage": {
            "save_json": True,
            "save_csv": True,
            "save_annotated_image": True,
            "json_filename": "result.json",
            "csv_filename": "result.csv",
            "annotated_image_filename": "result.jpg",
            "box_color": [0, 200, 0],
            "box_thickness": 2,
            "font_scale": 0.5,
        },
        "validation": {
            "iou_match_threshold": 0.30,
            "top_k": 5,
            "confidence_threshold": 0.0,
            "report_json_filename": "report.json",
            "report_csv_filename": "report.csv",
            "summary_filename": "summary.txt",
        },
    }
    config_dict["project_root"] = str(project_root)

    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "w", encoding="utf-8") as file_handle:
        yaml.safe_dump(config_dict, file_handle, allow_unicode=True)


@pytest.fixture
def test_config(tmp_path: Path) -> AppConfig:
    """Builds an isolated AppConfig rooted at a pytest tmp_path.

    Args:
        tmp_path: Pytest-provided temporary directory, unique per test.

    Returns:
        A validated AppConfig instance pointing entirely within tmp_path.
    """
    config_path = tmp_path / "configs" / "config.yaml"
    _write_default_config_yaml(config_path, tmp_path)

    with open(config_path, "r", encoding="utf-8") as file_handle:
        raw = yaml.safe_load(file_handle)
    config = AppConfig(**raw)
    _seed_test_catalog(config)
    return config


def _seed_test_catalog(config: AppConfig) -> None:
    """Catalog DB tối thiểu: "1" -> prod_red_square, "2" -> prod_blue_square (barcode, force barcode)."""
    from engine.catalog.db import CatalogMeta, Product, ProductEvidence, Session, create_all, make_engine

    engine = make_engine(config.resolve_path(config.catalog.db_path))
    try:
        create_all(engine)
        with Session(engine) as session:
            session.add(Product(product_id="1", product_name="prod_red_square", gallery_folder="prod_red_square"))
            session.add(Product(product_id="2", product_name="prod_blue_square", gallery_folder="prod_blue_square",
                                barcode="1234567890"))
            session.flush()
            session.add(ProductEvidence(product_id="2", evidence_type="force_evidence", value_json='["barcode"]'))
            session.add(CatalogMeta(key="next_product_id", value="3"))
            session.commit()
    finally:
        engine.dispose()


@pytest.fixture
def test_catalog(test_config: AppConfig):
    """Catalog trong bộ nhớ: SKU "1"–"9"; SKU "2" bắt buộc plugin barcode (force_evidence)."""
    from engine.catalog.repository import CatalogData, InMemoryCatalogRepository, ProductRecord

    products = [ProductRecord(product_id=str(i), product_name=f"P{i}") for i in range(1, 10)]
    evidence = {"2": {"force_evidence": ["barcode"]}}
    return InMemoryCatalogRepository(CatalogData.build(products, evidence, {}))


@pytest.fixture
def gallery_config(test_config: AppConfig) -> AppConfig:
    """Extends `test_config` with a two-product gallery + built catalog/index.

    Product IDs come from the catalog DB seeded by `test_config`:
    "1" -> prod_red_square, "2" -> prod_blue_square.

    Args:
        test_config: The isolated base AppConfig fixture.

    Returns:
        The same AppConfig, after populating its gallery, running
        sync_gallery + GalleryIndexBuilder (via BuildPipeline).
    """
    gallery_dir = test_config.resolve_path(test_config.paths.gallery_dir)

    red_dir = gallery_dir / "prod_red_square"
    red_dir.mkdir(parents=True, exist_ok=True)
    red_image = np.full((100, 100, 3), 255, dtype=np.uint8)
    red_image[10:90, 10:90] = (30, 30, 200)
    cv2.imwrite(str(red_dir / "01.png"), red_image)

    blue_dir = gallery_dir / "prod_blue_square"
    blue_dir.mkdir(parents=True, exist_ok=True)
    blue_image = np.full((100, 100, 3), 255, dtype=np.uint8)
    blue_image[10:90, 10:90] = (200, 60, 30)
    cv2.imwrite(str(blue_dir / "01.png"), blue_image)

    BuildPipeline(test_config).run()

    return test_config
