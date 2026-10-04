"""FastAPI dependency for the catalog service."""

from fastapi import Depends

from app.core.backends import Backends
from app.core.deps import backends, require
from app.modules.catalog.service import CatalogService


def catalog_service(b: Backends = Depends(backends)) -> CatalogService:
    return require(b.catalog, "Danh mục sản phẩm")
