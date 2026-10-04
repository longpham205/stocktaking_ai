"""Product search for the POS screen: by name without Vietnamese accents, by id, or by barcode."""

import unicodedata
from collections.abc import Iterable
from dataclasses import asdict

from app.core.errors import Conflict, Invalid, NotFound
from app.modules.audit.ports import ChangeEntry
from app.modules.audit.schemas import RevertIn
from app.modules.auth.ports import CurrentUser
from app.modules.catalog.ports import Product
from app.modules.catalog.repository import CatalogRepository
from app.modules.catalog.schemas import AdminProductsOut, ProductOut, ProductPatch

SEARCH_LIMIT = 50
ADMIN_FILTERS = ("", "missing_price", "missing_barcode", "needs_naming")


def fold(text: str) -> str:
    """Lower case without Vietnamese accents, so "phan ma hong" finds "Phấn má hồng"."""
    text = unicodedata.normalize("NFD", text.lower().replace("đ", "d"))
    return "".join(c for c in text if unicodedata.category(c) != "Mn")


def _catalog_order(product: Product) -> tuple[int, str]:
    # the engine's order: ids are numbers stored as text, so "2" comes before "10"
    return (len(product.id), product.id)


class CatalogService:
    def __init__(self, repo: CatalogRepository):
        self.repo = repo

    async def lookup(self, product_ids: Iterable[str]) -> dict[str, Product]:
        """Products by id, including the ones no longer on sale. An unknown id is absent."""
        return await self.repo.products_by_id(product_ids)

    async def search(self, search: str = "", barcode: str = "") -> list[Product]:
        """Products on sale, at most `SEARCH_LIMIT`. A barcode must match exactly and wins over
        `search`; `search` matches a part of the name or the whole id; neither lists the catalog."""
        barcode, query = barcode.strip(), fold(search.strip())
        found = []
        for product in sorted(await self.repo.active_products(), key=_catalog_order):
            if barcode:
                if product.barcode != barcode:
                    continue
            elif query and query not in fold(product.name) and query != product.id:
                continue
            found.append(product)
            if len(found) >= SEARCH_LIMIT:
                break
        return found

    # ---------------------------------------------------------------- admin

    async def admin_list(self, search: str = "", filter_: str = "", page: int = 1, size: int = 50) -> AdminProductsOut:
        """The catalog on sale for the admin screen: search by name, id or barcode (a part of any),
        filter what still needs work, one page at a time."""
        if filter_ not in ADMIN_FILTERS:
            raise Invalid("filter phải là missing_price|missing_barcode|needs_naming")
        products = sorted(await self.repo.active_products(), key=_catalog_order)
        query = fold(search.strip())
        rows = [
            p
            for p in products
            if (not query or query in fold(p.name) or query in p.id or query in p.barcode)
            and (filter_ != "missing_price" or p.price is None)
            and (filter_ != "missing_barcode" or not p.barcode)
            and (filter_ != "needs_naming" or p.needs_naming)
        ]
        page, size = max(1, page), max(1, min(200, size))
        return AdminProductsOut(
            total=len(rows),
            page=page,
            size=size,
            items=[ProductOut(**asdict(p)) for p in rows[(page - 1) * size : page * size]],
            missing_price=sum(1 for p in products if p.price is None),
            missing_barcode=sum(1 for p in products if not p.barcode),
            needs_naming=sum(1 for p in products if p.needs_naming),
        )

    async def _existing(self, product_id: str) -> Product:
        product = (await self.lookup([product_id])).get(product_id)
        if product is None:
            raise NotFound("Không thấy sản phẩm")
        return product

    async def update(self, current: CurrentUser, product_id: str, body: ProductPatch) -> ProductOut:
        await self._existing(product_id)
        if "price" not in body.model_fields_set:
            raise Invalid("Chỉ sửa được 'price'")
        await self.repo.set_price(product_id, body.price, current.user_id)
        return ProductOut(**asdict(await self._existing(product_id)))

    async def revert(self, current: CurrentUser, entry: ChangeEntry, _: RevertIn) -> None:
        """Change-log reverter for the `product` table (the price; barcode and name with 3e-2)."""
        if entry.field != "price":
            raise Invalid("Loại thay đổi này không hoàn tác được")
        product = (await self.lookup([entry.record_id])).get(entry.record_id)
        if product is None:
            raise NotFound("Sản phẩm không còn trong catalog")
        if (None if product.price is None else str(product.price)) != entry.new:
            raise Conflict("Giá trị đã được thay đổi sau lần sửa này, không thể hoàn tác", code="CHANGE_STALE")
        await self.repo.set_price(entry.record_id, None if entry.old is None else int(entry.old), current.user_id)
