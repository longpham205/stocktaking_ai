"""Postgres persistence for the catalog as the web sees it. The only place in the module that runs SQL.

The product tables are the engine's (backend/engine/catalog/db.py, built by migration 0002). They are
read here with plain SQL instead of through `engine.catalog`: an API process started with the fake
recognizer must not import the engine, and a query always sees what the admin last saved, with no
in-memory copy to reload.
"""

import json
from collections.abc import Iterable

from sqlalchemy import Boolean, ColumnElement, String, and_, column, delete, func, select, table
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine

from app.modules.audit.ports import Change
from app.modules.audit.repository import record_changes
from app.modules.catalog.models import ProductPriceRow
from app.modules.catalog.ports import Product

_PRICE = ProductPriceRow
_P = table(
    "product",
    column("product_id", String),
    column("product_name", String),
    column("barcode", String),
    column("is_active", Boolean),
    column("needs_naming", Boolean),
)
_E = table(
    "product_evidence",
    column("product_id", String),
    column("evidence_type", String),
    column("value_json", String),
)
_C = table("color_reference", column("color_code", String))


def _color_code(product_id: str, value_json: str | None) -> str | None:
    if value_json is None:
        return None
    try:
        value = json.loads(value_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"value_json hỏng ở bằng chứng color_code của SKU {product_id}: {exc}") from exc
    return str(value) if value else None


class CatalogRepository:
    def __init__(self, engine: AsyncEngine):
        self.engine = engine

    async def active_products(self) -> list[Product]:
        """Every product on sale with its current price, in no particular order."""
        return await self._products(_P.c.is_active)

    async def products_by_id(self, product_ids: Iterable[str]) -> dict[str, Product]:
        """The products with these ids, on sale or not: an old order still shows its names."""
        ids = list(product_ids)
        if not ids:
            return {}
        return {product.id: product for product in await self._products(_P.c.product_id.in_(ids))}

    async def set_price(self, product_id: str, price: int | None, changed_by: int) -> bool:
        """Set (or with None, remove) the price and log the change, in one transaction. False when
        the price was already that."""
        async with self.engine.begin() as conn:
            current = select(_PRICE.price).where(_PRICE.product_id == product_id).with_for_update()
            old = (await conn.execute(current)).scalar_one_or_none()
            if old == price:
                return False
            change = Change("price", None if old is None else str(old), None if price is None else str(price))
            await record_changes(conn, "product", product_id, [change], changed_by)
            if price is None:
                await conn.execute(delete(_PRICE).where(_PRICE.product_id == product_id))
            else:
                statement = insert(_PRICE).values(product_id=product_id, price=price)
                await conn.execute(
                    statement.on_conflict_do_update(
                        index_elements=[_PRICE.product_id], set_={"price": price, "updated_at": func.now()}
                    )
                )
        return True

    async def _products(self, condition: ColumnElement[bool]) -> list[Product]:
        query = (
            select(
                _P.c.product_id,
                _P.c.product_name,
                _P.c.barcode,
                _P.c.needs_naming,
                _PRICE.price,
                _E.c.value_json,
            )
            .select_from(_P)
            .outerjoin(_PRICE, _PRICE.product_id == _P.c.product_id)
            .outerjoin(_E, and_(_E.c.product_id == _P.c.product_id, _E.c.evidence_type == "color_code"))
            .where(condition)
        )
        async with self.engine.connect() as conn:
            rows = (await conn.execute(query)).mappings().all()
            colors = set((await conn.execute(select(_C.c.color_code))).scalars())
        products = []
        for row in rows:
            code = _color_code(row["product_id"], row["value_json"])
            products.append(
                Product(
                    id=row["product_id"],
                    name=row["product_name"],
                    barcode=row["barcode"] or "",
                    price=row["price"],
                    needs_naming=row["needs_naming"],
                    missing_color_reference=code is not None and code not in colors,
                )
            )
        return products
