"""`/api/catalog/*` (the product search of the POS screen), `/api/admin/products*` and
`/api/admin/colors*` (admins), and the signed gallery photos (`/api/gallery/...`, public)."""

from dataclasses import asdict

from fastapi import APIRouter, Depends, Response

from app.modules.auth.deps import current_user, require_admin
from app.modules.auth.ports import CurrentUser
from app.modules.catalog.deps import catalog_service
from app.modules.catalog.schemas import (
    AdminProductsOut,
    ColorOut,
    ColorPatch,
    ColorsOut,
    EvidenceOut,
    EvidencePatch,
    ProductOut,
    ProductPatch,
    ProductsOut,
)
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


@router.get("/admin/products/{product_id}/evidence", dependencies=[Depends(require_admin)])
async def product_evidence(product_id: str, service: CatalogService = Depends(catalog_service)) -> EvidenceOut:
    return await service.evidence(product_id)


@router.patch("/admin/products/{product_id}/evidence")
async def update_evidence(
    product_id: str,
    body: EvidencePatch,
    admin: CurrentUser = Depends(require_admin),
    service: CatalogService = Depends(catalog_service),
) -> EvidenceOut:
    return await service.update_evidence(admin, product_id, body)


@router.get("/admin/colors", dependencies=[Depends(require_admin)])
async def colors(service: CatalogService = Depends(catalog_service)) -> ColorsOut:
    return ColorsOut(items=await service.colors())


@router.patch("/admin/colors/{code}")
async def update_color(
    code: str,
    body: ColorPatch,
    admin: CurrentUser = Depends(require_admin),
    service: CatalogService = Depends(catalog_service),
) -> ColorOut:
    return await service.update_color(admin, code, body)


@router.get("/gallery/{product_id}/{index}", include_in_schema=False)
async def gallery_image(
    product_id: str, index: int, exp: int = 0, sig: str = "", service: CatalogService = Depends(catalog_service)
) -> Response:
    image = await service.gallery_image(product_id, index, exp, sig)
    return Response(image, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=300"})
