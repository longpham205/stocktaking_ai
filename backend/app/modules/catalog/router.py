"""`/api/catalog/*`: the product search of the POS screen."""

from dataclasses import asdict

from fastapi import APIRouter, Depends

from app.modules.auth.deps import current_user
from app.modules.catalog.deps import catalog_service
from app.modules.catalog.schemas import ProductOut, ProductsOut
from app.modules.catalog.service import CatalogService

router = APIRouter(tags=["catalog"], dependencies=[Depends(current_user)])


@router.get("/catalog/products")
async def products(
    search: str = "", barcode: str = "", service: CatalogService = Depends(catalog_service)
) -> ProductsOut:
    found = await service.search(search, barcode)
    return ProductsOut(items=[ProductOut(**asdict(product)) for product in found])
