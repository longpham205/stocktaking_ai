"""Persistent product catalog metadata builder.

Builds and reconciles product metadata from the current product gallery.
Unlike a full rebuild, this module preserves metadata that has been
manually edited in the existing JSON files.
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
    """Build and reconcile persistent product metadata."""

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._gallery_dir = config.resolve_path(config.paths.gallery_dir)
        self._metadata_dir = config.resolve_path(config.paths.metadata_dir)
        self._products_path = self._metadata_dir / config.catalog.products_filename
        self._product_ids_path = self._metadata_dir / config.catalog.product_ids_filename
        self._config_id_mapping = config.catalog.id_mapping

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    def build(self) -> list[dict[str, Any]]:
        """Build and reconcile the product catalog.

        Existing metadata is preserved whenever the corresponding gallery folder still exists.
        """
        logger.info("Reconciling product catalog from '%s'", self._gallery_dir)

        self._validate_gallery()
        self._validate_config_mapping()

        gallery_folders = self._scan_gallery()
        logger.info("Found %d product folder(s) in current gallery.", len(gallery_folders))

        existing_products = self._load_existing_products()
        existing_product_ids = self._load_existing_product_ids()

        products, product_id_map, next_internal_id = self._reconcile_products(
            gallery_folders=gallery_folders,
            existing_products=existing_products,
            existing_product_ids=existing_product_ids,
        )

        self._save_catalog(products)
        self._save_product_ids(product_id_map, next_internal_id=next_internal_id)

        logger.info(
            "Product catalog reconciled successfully: %d product(s) -> '%s'",
            len(products),
            self._products_path,
        )
        logger.info(
            "Product ID mapping reconciled successfully: %d mapping(s) -> '%s'",
            len(product_id_map),
            self._product_ids_path,
        )

        return products

    # -------------------------------------------------------------------------
    # Gallery scanning
    # -------------------------------------------------------------------------

    def _scan_gallery(self) -> list[dict[str, Any]]:
        """Scan the current gallery folders."""
        product_dirs = sorted(path for path in self._gallery_dir.iterdir() if path.is_dir())
        return [
            {
                "folder": product_dir.name,
                "image_count": self._count_images(product_dir),
            }
            for product_dir in product_dirs
        ]

    @staticmethod
    def _count_images(product_dir: Path) -> int:
        """Count supported image files in a product folder."""
        return sum(
            1
            for path in product_dir.iterdir()
            if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
        )

    # -------------------------------------------------------------------------
    # Reconciliation
    # -------------------------------------------------------------------------

    def _reconcile_products(
        self,
        gallery_folders: list[dict[str, Any]],
        existing_products: list[dict[str, Any]],
        existing_product_ids: dict[str, str],
    ) -> tuple[list[dict[str, Any]], dict[str, str], int]:
        """Reconcile existing metadata with the current gallery."""
        existing_by_folder = self._index_existing_products(existing_products)
        configured_folder_to_id = self._build_config_folder_to_id()

        used_ids = self._collect_historical_ids(
            existing_products=existing_products,
            existing_product_ids=existing_product_ids,
        )

        next_internal_id = max(used_ids, default=0) + 1
        configured_ids = {int(raw_id) for raw_id in self._config_id_mapping}

        if configured_ids:
            next_internal_id = max(next_internal_id, max(configured_ids) + 1)

        products: list[dict[str, Any]] = []
        product_id_map: dict[str, str] = {}
        current_folders = {entry["folder"] for entry in gallery_folders}

        # Log removed products
        removed_folders = set(existing_by_folder.keys()) - current_folders
        for folder in sorted(removed_folders):
            old_product = existing_by_folder[folder]
            old_id = old_product.get("product_id", "?")
            logger.info("Removing deleted gallery product: ID %s -> '%s'", old_id, folder)

        # Reconcile current gallery
        assigned_ids: set[int] = set()

        for entry in gallery_folders:
            folder_name = entry["folder"]
            image_count = entry["image_count"]

            existing_product = existing_by_folder.get(folder_name)
            configured_id = configured_folder_to_id.get(folder_name)

            if existing_product is not None:
                product = dict(existing_product)
                old_id = self._parse_product_id(
                    product.get("product_id"),
                    context=f"existing product '{folder_name}'",
                )

                if configured_id is not None and old_id != configured_id:
                    logger.warning(
                        "Configured product ID overrides existing ID: '%s': %d -> %d",
                        folder_name,
                        old_id,
                        configured_id,
                    )
                    internal_id = configured_id
                else:
                    internal_id = old_id

                # Preserve manually edited metadata; only image_count is updated directly.
                product["product_id"] = str(internal_id)
                product["folder"] = folder_name
                product["image_count"] = image_count

                logger.info("Preserving existing product ID %d -> '%s'", internal_id, folder_name)

            else:
                if configured_id is not None:
                    internal_id = configured_id
                    logger.info("Using configured product ID %d -> '%s'", internal_id, folder_name)

                    if internal_id in used_ids:
                        historical_folder = self._find_historical_folder(
                            internal_id,
                            existing_products,
                            existing_product_ids,
                        )
                        if historical_folder != folder_name:
                            raise ValueError(
                                f"Configured product ID {internal_id} for '{folder_name}' is "
                                f"already associated with another product '{historical_folder}'."
                            )
                else:
                    while next_internal_id in used_ids:
                        next_internal_id += 1

                    internal_id = next_internal_id
                    next_internal_id += 1
                    logger.info("Assigned NEW product ID %d -> '%s'", internal_id, folder_name)

                product = self._create_new_product(
                    internal_id=internal_id,
                    folder_name=folder_name,
                    image_count=image_count,
                )

            if internal_id in assigned_ids:
                raise ValueError(
                    f"Duplicate internal product ID {internal_id} detected "
                    f"while reconciling product '{folder_name}'."
                )

            assigned_ids.add(internal_id)
            product_id_str = str(internal_id)

            products.append(product)
            product_id_map[product_id_str] = folder_name
            used_ids.add(internal_id)

        # Presentation/storage sorting
        products.sort(key=lambda p: int(p["product_id"]))
        product_id_map = dict(sorted(product_id_map.items(), key=lambda item: int(item[0])))

        next_internal_id = max(next_internal_id, max(used_ids, default=0) + 1)

        return products, product_id_map, next_internal_id

    @staticmethod
    def _create_new_product(internal_id: int, folder_name: str, image_count: int) -> dict[str, Any]:
        """Create metadata for a newly discovered gallery product."""
        return {
            "product_id": str(internal_id),
            "product_name": folder_name,
            "brand": "",
            "category": "",
            "barcode": "",
            "description": "",
            "folder": folder_name,
            "image_count": image_count,
        }

    # -------------------------------------------------------------------------
    # Existing metadata loading
    # -------------------------------------------------------------------------

    def _load_existing_products(self) -> list[dict[str, Any]]:
        """Load the existing products.json."""
        if not self._products_path.exists():
            logger.info("No existing products.json found. Starting with empty catalog.")
            return []

        try:
            with self._products_path.open("r", encoding="utf-8") as file_handle:
                data = json.load(file_handle)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON in products metadata: {self._products_path}") from exc

        if not isinstance(data, list):
            raise ValueError(f"Expected products.json to contain a list: {self._products_path}")

        for index, product in enumerate(data):
            if not isinstance(product, dict):
                raise ValueError(f"Invalid product entry at index {index} in {self._products_path}: expected object.")

            if not product.get("folder"):
                raise ValueError(f"Product entry at index {index} in {self._products_path} has no valid 'folder'.")

            if "product_id" not in product:
                raise ValueError(f"Product '{product.get('folder')}' in {self._products_path} has no 'product_id'.")

            self._parse_product_id(
                product["product_id"],
                context=f"products.json entry '{product['folder']}'",
            )

        return data

    def _load_existing_product_ids(self) -> dict[str, str]:
        """Load the existing product_ids.json."""
        if not self._product_ids_path.exists():
            logger.info("No existing product_ids.json found. Starting with empty ID registry.")
            return {}

        try:
            with self._product_ids_path.open("r", encoding="utf-8") as file_handle:
                data = json.load(file_handle)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON in product ID metadata: {self._product_ids_path}") from exc

        if not isinstance(data, dict):
            raise ValueError(f"Expected product_ids.json to contain an object: {self._product_ids_path}")

        products = data.get("products", {})
        if not isinstance(products, dict):
            raise ValueError(f"'products' in {self._product_ids_path} must be an object.")

        normalized: dict[str, str] = {}
        for raw_id, folder in products.items():
            internal_id = self._parse_product_id(raw_id, context="product_ids.json")
            if not isinstance(folder, str) or not folder:
                raise ValueError(f"Invalid folder for product ID {raw_id} in {self._product_ids_path}.")
            normalized[str(internal_id)] = folder

        return normalized

    # -------------------------------------------------------------------------
    # Helper indexing & validation methods
    # -------------------------------------------------------------------------

    @staticmethod
    def _index_existing_products(products: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        indexed: dict[str, dict[str, Any]] = {}
        for product in products:
            folder = product["folder"]
            if folder in indexed:
                raise ValueError(f"Duplicate product folder found in products.json: '{folder}'.")
            indexed[folder] = product
        return indexed

    @staticmethod
    def _collect_historical_ids(
        existing_products: list[dict[str, Any]],
        existing_product_ids: dict[str, str],
    ) -> set[int]:
        used_ids: set[int] = set()
        for product in existing_products:
            if product.get("product_id") is not None:
                used_ids.add(int(product["product_id"]))

        for raw_id in existing_product_ids:
            used_ids.add(int(raw_id))

        return used_ids

    @staticmethod
    def _find_historical_folder(
        internal_id: int,
        existing_products: list[dict[str, Any]],
        existing_product_ids: dict[str, str],
    ) -> str | None:
        id_str = str(internal_id)
        for product in existing_products:
            if str(product.get("product_id")) == id_str:
                return product.get("folder")
        return existing_product_ids.get(id_str)

    def _validate_config_mapping(self) -> None:
        seen_names: dict[str, str] = {}
        seen_ids: set[int] = set()

        for raw_id, product_name in self._config_id_mapping.items():
            try:
                internal_id = int(raw_id)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"Invalid product ID in catalog.id_mapping: '{raw_id}'. IDs must be numeric strings."
                ) from exc

            if internal_id <= 0:
                raise ValueError(f"Product IDs must be positive integers. Invalid ID: {raw_id}")

            if internal_id in seen_ids:
                raise ValueError(f"Duplicate product ID detected in catalog.id_mapping: {internal_id}")

            seen_ids.add(internal_id)

            if not isinstance(product_name, str) or not product_name.strip():
                raise ValueError(
                    f"Product name in catalog.id_mapping must be a non-empty string. Invalid value for ID {raw_id}: {product_name!r}"
                )

            previous_id = seen_names.get(product_name)
            if previous_id is not None:
                raise ValueError(
                    f"Duplicate product name detected in catalog.id_mapping: '{product_name}' is mapped "
                    f"to both ID {previous_id} and ID {raw_id}."
                )

            seen_names[product_name] = str(internal_id)

    def _build_config_folder_to_id(self) -> dict[str, int]:
        return {folder_name: int(raw_id) for raw_id, folder_name in self._config_id_mapping.items()}

    def _save_catalog(self, products: list[dict[str, Any]]) -> None:
        self._metadata_dir.mkdir(parents=True, exist_ok=True)
        with self._products_path.open("w", encoding="utf-8") as file_handle:
            json.dump(products, file_handle, ensure_ascii=False, indent=2)
            file_handle.write("\n")

    def _save_product_ids(self, product_id_map: dict[str, str], next_internal_id: int) -> None:
        self._metadata_dir.mkdir(parents=True, exist_ok=True)
        data = {"next_id": next_internal_id, "products": product_id_map}
        # Sửa bug: dùng _product_ids_path thay vì _products_path
        with self._product_ids_path.open("w", encoding="utf-8") as file_handle:
            json.dump(data, file_handle, ensure_ascii=False, indent=2)
            file_handle.write("\n")

    @staticmethod
    def _parse_product_id(raw_id: Any, context: str) -> int:
        try:
            internal_id = int(raw_id)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid product ID '{raw_id}' in {context}. IDs must be positive integers.") from exc

        if internal_id <= 0:
            raise ValueError(f"Product ID must be positive in {context}: {raw_id}")

        return internal_id

    def _validate_gallery(self) -> None:
        if not self._gallery_dir.exists():
            raise FileNotFoundError(f"Gallery directory does not exist: {self._gallery_dir}")

        if not self._gallery_dir.is_dir():
            raise NotADirectoryError(f"Gallery path is not a directory: {self._gallery_dir}")


def build_product_metadata(config: AppConfig) -> list[dict[str, Any]]:
    """Build and reconcile persistent product metadata."""
    return MetadataBuilder(config).build()