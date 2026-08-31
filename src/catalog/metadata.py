"""Product catalog metadata builder.

Builds the product catalog and stable internal product IDs from the
current product gallery.

ID assignment rules:

1. IDs declared in config.catalog.id_mapping have highest priority.
2. Existing products found in the config keep their configured IDs.
3. New gallery products receive new IDs sequentially after the highest
   configured ID.
4. Gallery folder names remain the real product identifiers (stored in
   the "folder" field).
5. Removed gallery products are removed from generated JSON files.
6. Existing product IDs are never reassigned because of gallery sorting.
7. The generated JSON files are completely overwritten on every build.
8. This module does NOT build the FAISS index.

Identity convention (important - see module docstring of retriever.py):
    Every DTO/JSON field named "product_id" throughout this codebase
    holds the **stable internal numeric ID as a string** (e.g. "1", "2",
    "20"), never the gallery folder name / display name. The folder name
    and display name are carried separately as "folder" / "product_name".

Gallery structure:

    data/gallery/
    ├── 1000000008/
    ├── 1000000009/
    ├── マジョリカマジョルカ　シャドーカスタマイズ（BE203）/
    └── ...

Generated files:

    data/metadata/products.json
    data/metadata/product_ids.json

config.yaml:

    catalog:
      id_mapping:
        "1": "アネッサ　パーフェクトUV　スキンケアスプレー"
        "2": "インテグレート　アイブローペンシル（BR641）"
        ...
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.core.config import AppConfig
from src.core.logger import get_logger

logger = get_logger(__name__)


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


class MetadataBuilder:
    """Build the product catalog from the current product gallery."""

    def __init__(self, config: AppConfig) -> None:
        """Initialize the metadata builder.

        Args:
            config: Fully validated application configuration.
        """
        self._config = config
        self._gallery_dir = config.resolve_path(config.paths.gallery_dir)
        self._metadata_dir = config.resolve_path(config.paths.metadata_dir)
        self._products_path = self._metadata_dir / config.catalog.products_filename
        self._product_ids_path = self._metadata_dir / config.catalog.product_ids_filename
        self._config_id_mapping = config.catalog.id_mapping

    # =========================================================================
    # Public API
    # =========================================================================

    def build(self) -> list[dict[str, Any]]:
        """Build product metadata from the current gallery.

        ID assignment follows these rules:

        - Products found in config.catalog.id_mapping keep their
          configured IDs.
        - New products receive IDs sequentially after the highest
          configured ID.
        - Removed products are excluded from the generated JSON files.
        - The generated JSON files are completely overwritten.

        Returns:
            Newly generated product catalog. Each entry's "product_id" is
            the stable internal numeric ID as a string.
        """
        logger.info("Building product catalog from '%s'", self._gallery_dir)

        self._validate_gallery()
        self._validate_config_mapping()

        gallery_folders = self._scan_gallery()
        logger.info("Found %d product folder(s) in current gallery.", len(gallery_folders))

        products, product_id_map = self._assign_product_ids(gallery_folders)

        self._save_catalog(products)

        next_internal_id = max((int(pid) for pid in product_id_map), default=0) + 1
        self._save_product_ids(product_id_map, next_internal_id=next_internal_id)

        logger.info(
            "Product catalog rebuilt successfully: %d product(s) -> '%s'",
            len(products),
            self._products_path,
        )
        logger.info(
            "Product ID mapping rebuilt successfully: %d mapping(s) -> '%s'",
            len(product_id_map),
            self._product_ids_path,
        )

        return products

    # =========================================================================
    # Gallery scanning
    # =========================================================================

    def _scan_gallery(self) -> list[dict[str, Any]]:
        """Scan the current gallery folders.

        Folder names are sorted only for deterministic processing.
        Sorting does NOT determine internal IDs; IDs are assigned later
        using config priority.

        Returns:
            List of raw folder descriptors (folder name + image count),
            without any internal_id assigned yet.
        """
        product_dirs = sorted(path for path in self._gallery_dir.iterdir() if path.is_dir())

        folders: list[dict[str, Any]] = []
        for product_dir in product_dirs:
            folder_name = product_dir.name
            folders.append(
                {
                    "folder": folder_name,
                    "image_count": self._count_images(product_dir),
                }
            )
        return folders

    @staticmethod
    def _count_images(product_dir: Path) -> int:
        """Counts supported image files in a product folder."""
        return sum(
            1
            for path in product_dir.iterdir()
            if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
        )

    # =========================================================================
    # Product ID assignment
    # =========================================================================

    def _assign_product_ids(
        self,
        gallery_folders: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], dict[str, str]]:
        """Assign stable internal IDs to gallery products.

        Priority:
            1. Configured ID mapping (config.catalog.id_mapping).
            2. New sequential IDs after the highest configured ID.

        Args:
            gallery_folders: Raw folder descriptors from `_scan_gallery`.

        Returns:
            Tuple of:
                - products: catalog entries. "product_id" is the stable
                  internal numeric ID (string); "folder"/"product_name"
                  carry the gallery folder name.
                - product_id_map: {internal_id(str): folder_name(str)}.
        """
        configured_folder_to_id: dict[str, int] = {
            folder_name: int(raw_id) for raw_id, folder_name in self._config_id_mapping.items()
        }

        next_internal_id = max((int(pid) for pid in self._config_id_mapping), default=0) + 1

        products: list[dict[str, Any]] = []
        product_id_map: dict[str, str] = {}
        assigned_ids: set[int] = set()

        for entry in gallery_folders:
            folder_name = entry["folder"]

            if folder_name in configured_folder_to_id:
                internal_id = configured_folder_to_id[folder_name]
                logger.info("Using configured product ID %d -> '%s'", internal_id, folder_name)
            else:
                internal_id = next_internal_id
                next_internal_id += 1
                logger.info("Assigned NEW product ID %d -> '%s'", internal_id, folder_name)

            if internal_id in assigned_ids:
                raise ValueError(
                    f"Duplicate internal product ID {internal_id} detected while "
                    f"building product metadata for '{folder_name}'."
                )
            assigned_ids.add(internal_id)

            product_id_str = str(internal_id)

            products.append(
                {
                    "product_id": product_id_str,
                    "product_name": folder_name,
                    "brand": "",
                    "category": "",
                    "barcode": "",
                    "description": "",
                    "folder": folder_name,
                    "image_count": entry["image_count"],
                }
            )
            product_id_map[product_id_str] = folder_name

        products.sort(key=lambda product: int(product["product_id"]))
        product_id_map = dict(sorted(product_id_map.items(), key=lambda item: int(item[0])))

        return products, product_id_map

    # =========================================================================
    # Config validation
    # =========================================================================

    def _validate_config_mapping(self) -> None:
        """Validates the configured product ID mapping.

        Raises:
            ValueError: If an ID is non-numeric/non-positive, a name is
                empty, or two IDs map to the same product name.
        """
        seen_names: dict[str, str] = {}

        for raw_id, product_name in self._config_id_mapping.items():
            try:
                internal_id = int(raw_id)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"Invalid product ID in catalog.id_mapping: '{raw_id}'. "
                    "IDs must be numeric strings."
                ) from exc

            if internal_id <= 0:
                raise ValueError(f"Product IDs must be positive integers. Invalid ID: {raw_id}")

            if not isinstance(product_name, str) or not product_name.strip():
                raise ValueError(
                    "Product name in catalog.id_mapping must be a non-empty string. "
                    f"Invalid value for ID {raw_id}: {product_name!r}"
                )

            previous_id = seen_names.get(product_name)
            if previous_id is not None:
                raise ValueError(
                    f"Duplicate product name detected in catalog.id_mapping: "
                    f"'{product_name}' is mapped to both ID {previous_id} and ID {raw_id}."
                )
            seen_names[product_name] = str(internal_id)

    # =========================================================================
    # Persistence
    # =========================================================================

    def _save_product_ids(self, product_id_map: dict[str, str], next_internal_id: int) -> None:
        """Overwrites product_ids.json completely."""
        self._metadata_dir.mkdir(parents=True, exist_ok=True)

        data = {"next_id": next_internal_id, "products": product_id_map}

        with self._product_ids_path.open("w", encoding="utf-8") as file_handle:
            json.dump(data, file_handle, ensure_ascii=False, indent=2)
            file_handle.write("\n")

    def _save_catalog(self, products: list[dict[str, Any]]) -> None:
        """Overwrites products.json completely."""
        self._metadata_dir.mkdir(parents=True, exist_ok=True)

        with self._products_path.open("w", encoding="utf-8") as file_handle:
            json.dump(products, file_handle, ensure_ascii=False, indent=2)
            file_handle.write("\n")

    # =========================================================================
    # Validation
    # =========================================================================

    def _validate_gallery(self) -> None:
        """Validates the gallery directory exists."""
        if not self._gallery_dir.exists():
            raise FileNotFoundError(f"Gallery directory does not exist: {self._gallery_dir}")
        if not self._gallery_dir.is_dir():
            raise NotADirectoryError(f"Gallery path is not a directory: {self._gallery_dir}")


def build_product_metadata(config: AppConfig) -> list[dict[str, Any]]:
    """Convenience function: build and completely overwrite product metadata.

    Args:
        config: Fully validated application configuration.

    Returns:
        Newly generated product catalog.
    """
    return MetadataBuilder(config).build()
