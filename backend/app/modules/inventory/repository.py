"""Postgres persistence for the stock on hand. The only place that writes `product_stock`.

Both functions run on the caller's connection, so a quantity commits or rolls back with what caused
it: `move_stock` with the order that was paid or voided, `set_stock_sync` with the change-log entry
of an admin's count. A history of movements, when one is added, is written here.
"""

from collections.abc import Mapping

from sqlalchemy import Connection, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from app.modules.inventory.models import ProductStockRow

_S = ProductStockRow


async def move_stock(conn: AsyncConnection, deltas: Mapping[str, int]) -> None:
    """Add each delta to its product's quantity (negative: sold). A product that is not tracked
    is left alone. Rows are taken in id order, so two orders paid at once cannot deadlock."""
    for product_id in sorted(deltas):
        if deltas[product_id]:
            await conn.execute(
                update(_S)
                .where(_S.product_id == product_id)
                .values(quantity=_S.quantity + deltas[product_id], updated_at=func.now())
            )


def set_stock_sync(conn: Connection, product_id: str, quantity: int | None) -> int | None:
    """Set what an admin counted (None: stop tracking the product). Returns the quantity before.
    Synchronous: it shares the transaction of `catalog.repository.CatalogEdits`."""
    current = select(_S.quantity).where(_S.product_id == product_id).with_for_update()
    old = conn.execute(current).scalar_one_or_none()
    if old == quantity:
        return old
    if quantity is None:
        conn.execute(delete(_S).where(_S.product_id == product_id))
    else:
        statement = insert(_S).values(product_id=product_id, quantity=quantity)
        conn.execute(
            statement.on_conflict_do_update(
                index_elements=[_S.product_id], set_={"quantity": quantity, "updated_at": func.now()}
            )
        )
    return old
