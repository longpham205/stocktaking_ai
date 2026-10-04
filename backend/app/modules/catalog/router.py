"""`/api/catalog/*`: the product search of the POS screen."""

from dataclasses import asdict

from fastapi import APIRouter, Depends

from app.modules.auth.deps import current_user, require_admin
from app.modules.auth.ports import CurrentUser
from app.modules.catalog.deps import catalog_service
from app.modules.catalog.schemas import AdminProductsOut, ProductOut, ProductPatch, ProductsOut
from app.modules.catalog.service import CatalogService

router = APIRouter(tags=["catalog"])


@router.get("/catalog/products", dependencies=[Depends(current_user)])
async def products(
    search: str = "", barcode: str = "", service: CatalogService = Depends(catalog_service)
) -> ProductsOut:
    found = await service.search(search, barcode)
    return ProductsOut(items=[ProductOut(**asdict(product)) for product in found])


@router.get("/admin/products", dependencies=[Depends(require_admin)])
async def admin_products(
    search: str = "",
    filter: str = "",  # noqa: A002  the legacy query parameter's name
    page: int = 1,
    size: int = 50,
    service: CatalogService = Depends(catalog_service),
) -> AdminProductsOut:
    return await service.admin_list(search, filter, page, size)


@router.patch("/admin/products/{product_id}")
async def update_product(
    product_id: str,
    body: ProductPatch,
    admin: CurrentUser = Depends(require_admin),
    service: CatalogService = Depends(catalog_service),
) -> ProductOut:
    return await service.update(admin, product_id, body)
