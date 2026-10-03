"""Chọn nguồn catalog theo ``catalog.source`` (không tự chuyển nguồn khi lỗi).

- ``sqlite``   -> ``SqliteCatalogRepository(catalog.db_path)``
- ``snapshot`` -> ``JsonSnapshotCatalogRepository(catalog.snapshot_path)``
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.catalog.repository import BaseCatalogRepository

if TYPE_CHECKING:
    from src.core.config import AppConfig


def open_catalog_repository(config: "AppConfig") -> BaseCatalogRepository:
    source = config.catalog.source
    if source == "sqlite":
        from src.catalog.repository import SqliteCatalogRepository

        return SqliteCatalogRepository(config.resolve_path(config.catalog.db_path))
    if source == "snapshot":
        from src.catalog.snapshot import JsonSnapshotCatalogRepository

        return JsonSnapshotCatalogRepository(config.resolve_path(config.catalog.snapshot_path))
    raise ValueError(f"catalog.source không hỗ trợ: {source!r}")


__all__ = ["open_catalog_repository"]
