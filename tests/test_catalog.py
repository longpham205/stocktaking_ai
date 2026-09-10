"""Unit tests for src.catalog.metadata.MetadataBuilder and gallery_builder.

Includes a regression test for the product_id identity bug found during
development: `products.json["product_id"]` must be the stable internal
numeric ID, never the gallery folder name.
"""

from __future__ import annotations

import json

from src.catalog.metadata import MetadataBuilder
from src.pipeline.build import BuildPipeline


def test_metadata_builder_assigns_configured_ids(test_config) -> None:
    """Products present in catalog.id_mapping must receive their configured IDs."""
    gallery_dir = test_config.resolve_path(test_config.paths.gallery_dir)
    (gallery_dir / "prod_red_square").mkdir(parents=True, exist_ok=True)
    (gallery_dir / "prod_blue_square").mkdir(parents=True, exist_ok=True)

    products = MetadataBuilder(test_config).build()

    by_id = {p["product_id"]: p for p in products}
    assert by_id["1"]["folder"] == "prod_red_square"
    assert by_id["2"]["folder"] == "prod_blue_square"


def test_metadata_builder_product_id_is_never_folder_name(test_config) -> None:
    """Regression test: product_id must be the numeric internal ID, not a folder name.

    A folder not present in config.catalog.id_mapping must still receive
    a *numeric* product_id (sequential, after the highest configured ID),
    never the raw folder string.
    """
    gallery_dir = test_config.resolve_path(test_config.paths.gallery_dir)
    (gallery_dir / "prod_red_square").mkdir(parents=True, exist_ok=True)
    (gallery_dir / "brand_new_unmapped_product").mkdir(parents=True, exist_ok=True)

    products = MetadataBuilder(test_config).build()

    by_folder = {p["folder"]: p for p in products}
    new_product_id = by_folder["brand_new_unmapped_product"]["product_id"]

    assert new_product_id.isdigit()
    assert new_product_id != "brand_new_unmapped_product"
    assert int(new_product_id) > max(int(pid) for pid in test_config.catalog.id_mapping)


def test_metadata_builder_writes_matching_product_ids_json(test_config) -> None:
    """product_ids.json and products.json must agree on the same ID<->folder mapping."""
    gallery_dir = test_config.resolve_path(test_config.paths.gallery_dir)
    (gallery_dir / "prod_red_square").mkdir(parents=True, exist_ok=True)

    MetadataBuilder(test_config).build()

    product_ids_path = (
        test_config.resolve_path(test_config.paths.metadata_dir) / test_config.catalog.product_ids_filename
    )
    with product_ids_path.open("r", encoding="utf-8") as file_handle:
        data = json.load(file_handle)

    assert data["products"]["1"] == "prod_red_square"


def test_build_pipeline_produces_consistent_gallery_index(gallery_config) -> None:
    """BuildPipeline output (products.json + gallery_metadata.json) must be internally consistent."""
    products_path = (
        gallery_config.resolve_path(gallery_config.paths.metadata_dir) / gallery_config.catalog.products_filename
    )
    gallery_metadata_path = gallery_config.resolve_path(gallery_config.retrieval.gallery_metadata_path)

    with products_path.open("r", encoding="utf-8") as file_handle:
        products = json.load(file_handle)
    with gallery_metadata_path.open("r", encoding="utf-8") as file_handle:
        gallery_metadata = json.load(file_handle)

    product_ids_in_catalog = {p["product_id"] for p in products}
    product_ids_in_gallery = {str(entry["product_id"]) for entry in gallery_metadata}

    # Every FAISS vector's product_id must resolve in the catalog.
    assert product_ids_in_gallery.issubset(product_ids_in_catalog)
    # And it must be the numeric ID, never a raw folder/display name.
    assert all(pid.isdigit() for pid in product_ids_in_gallery)
