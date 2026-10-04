"""Postgres persistence for orders and their lines. SQLAlchemy Core on a connection, no ORM session.
The only place in the module that runs SQL.

The service composes these statements inside one unit of work (`read()` or `write()`), so a rule
that spans several statements (merge two lines, pay an order) commits or rolls back as a whole.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

from sqlalchemy import RowMapping, delete, exists, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.modules.auth.models import ShiftRow, UserRow
from app.modules.captures.models import CaptureRow
from app.modules.orders.models import OrderItemRow, OrderRow
from app.modules.orders.ports import HistoryEntry, Order, OrderItem

_O = OrderRow
_I = OrderItemRow


def _order(row: RowMapping) -> Order:
    return Order(
        id=row["id"],
        shift_id=row["shift_id"],
        cashier_id=row["cashier_id"],
        status=row["status"],
        payment_method=row["payment_method"],
        cash_given=row["cash_given"],
        change_given=row["change_given"],
        total_amount=row["total_amount"],
        created_at=row["created_at"],
        paid_at=row["paid_at"],
    )


def _item(row: RowMapping) -> OrderItem:
    return OrderItem(
        id=row["id"],
        order_id=row["order_id"],
        product_id=row["product_id"],
        quantity=row["quantity"],
        unit_price=row["unit_price"],
        manual_price=row["manual_price"],
        flagged=row["flagged"],
        thumb_path=row["thumb_path"],
        evidence=row["evidence"],
    )


class OrdersUnit:
    """The statements of the module, on one connection."""

    def __init__(self, conn: AsyncConnection):
        self.conn = conn

    async def order(self, order_id: int, lock: bool = False) -> Order | None:
        """`lock`: hold the row until the transaction ends, so two requests changing the same
        order (a double click on Pay, a line added during checkout) run one after the other."""
        query = select(_O).where(_O.id == order_id)
        row = (await self.conn.execute(query.with_for_update() if lock else query)).mappings().first()
        return _order(row) if row else None

    async def latest_open_order(self, cashier_id: int) -> Order | None:
        query = select(_O).where(_O.cashier_id == cashier_id, _O.status == "open").order_by(_O.id.desc()).limit(1)
        row = (await self.conn.execute(query)).mappings().first()
        return _order(row) if row else None

    async def empty_open_order_id(self, cashier_id: int) -> int | None:
        """The cashier's newest open order that has neither a line nor a capture."""
        query = (
            select(_O.id)
            .where(
                _O.cashier_id == cashier_id,
                _O.status == "open",
                ~exists().where(_I.order_id == _O.id),
                ~exists().where(CaptureRow.order_id == _O.id),
            )
            .order_by(_O.id.desc())
            .limit(1)
        )
        return (await self.conn.execute(query)).scalar_one_or_none()

    async def create_order(self, cashier_id: int, shift_id: int) -> int:
        created = await self.conn.execute(insert(_O).values(cashier_id=cashier_id, shift_id=shift_id).returning(_O.id))
        return int(created.scalar_one())

    async def update_order(self, order_id: int, **values: Any) -> None:
        await self.conn.execute(update(_O).where(_O.id == order_id).values(**values))

    async def mark_paid(
        self, order_id: int, method: str, cash_given: int | None, change: int | None, total: int
    ) -> None:
        await self.update_order(
            order_id,
            status="paid",
            payment_method=method,
            cash_given=cash_given,
            change_given=change,
            total_amount=total,
            paid_at=func.now(),
        )

    async def items(self, order_id: int) -> list[OrderItem]:
        rows = (await self.conn.execute(select(_I).where(_I.order_id == order_id).order_by(_I.id))).mappings().all()
        return [_item(row) for row in rows]

    async def item(self, order_id: int, item_id: int) -> OrderItem | None:
        query = select(_I).where(_I.id == item_id, _I.order_id == order_id)
        row = (await self.conn.execute(query)).mappings().first()
        return _item(row) if row else None

    async def mergeable_item(self, order_id: int, product_id: str, other_than: int | None = None) -> OrderItem | None:
        """The order's first confirmed line of this product at the catalog price: the line more of
        the same product is added to."""
        query = select(_I).where(
            _I.order_id == order_id, _I.product_id == product_id, ~_I.flagged, _I.manual_price.is_(None)
        )
        if other_than is not None:
            query = query.where(_I.id != other_than)
        row = (await self.conn.execute(query.order_by(_I.id).limit(1))).mappings().first()
        return _item(row) if row else None

    async def insert_item(self, order_id: int, product_id: str, quantity: int) -> int:
        created = await self.conn.execute(
            insert(_I).values(order_id=order_id, product_id=product_id, quantity=quantity).returning(_I.id)
        )
        return int(created.scalar_one())

    async def update_item(self, item_id: int, **values: Any) -> None:
        await self.conn.execute(update(_I).where(_I.id == item_id).values(**values))

    async def delete_item(self, order_id: int, item_id: int) -> bool:
        deleted = await self.conn.execute(delete(_I).where(_I.id == item_id, _I.order_id == order_id))
        return bool(deleted.rowcount)

    async def add_to_shift(self, shift_id: int, amount: int) -> None:
        """Add to what the shift collected (a negative amount takes back a voided sale, never below 0)."""
        collected = func.greatest(0, ShiftRow.total_collected + amount)
        await self.conn.execute(update(ShiftRow).where(ShiftRow.id == shift_id).values(total_collected=collected))

    async def history(self, cashier_id: int | None, since: datetime | None, limit: int) -> list[HistoryEntry]:
        """Paid and voided orders, newest first; of one cashier, or of everyone when `cashier_id` is None."""
        item_count = (
            select(func.coalesce(func.sum(_I.quantity), 0)).where(_I.order_id == _O.id).scalar_subquery()
        ).label("item_count")
        query = (
            select(_O, UserRow.full_name.label("cashier_name"), item_count)
            .join(UserRow, UserRow.id == _O.cashier_id)
            .where(_O.status.in_(("paid", "void")))
        )
        if cashier_id is not None:
            query = query.where(_O.cashier_id == cashier_id)
        if since is not None:
            query = query.where(_O.created_at >= since)
        rows = (await self.conn.execute(query.order_by(_O.id.desc()).limit(limit))).mappings().all()
        return [
            HistoryEntry(order=_order(row), cashier_name=row["cashier_name"], item_count=int(row["item_count"]))
            for row in rows
        ]


class OrdersRepository:
    def __init__(self, engine: AsyncEngine):
        self.engine = engine

    @asynccontextmanager
    async def read(self) -> AsyncIterator[OrdersUnit]:
        async with self.engine.connect() as conn:
            yield OrdersUnit(conn)

    @asynccontextmanager
    async def write(self) -> AsyncIterator[OrdersUnit]:
        """One transaction: committed when the block ends, rolled back when it raises."""
        async with self.engine.begin() as conn:
            yield OrdersUnit(conn)
