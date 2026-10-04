"""Product search for the POS screen: by name without Vietnamese accents, by id, or by barcode."""

import unicodedata

from app.modules.catalog.ports import Product
from app.modules.catalog.repository import CatalogRepository

SEARCH_LIMIT = 50


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
