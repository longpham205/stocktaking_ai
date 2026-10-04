"""Postgres persistence for captures. The only place in the module that runs SQL.

A recognised capture becomes order lines in the same transaction as its own result: the unit of
work carries the orders statements (`unit.orders`) on the same connection.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import RowMapping, insert, select, update
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.modules.captures.models import CaptureRow
from app.modules.captures.ports import Capture
from app.modules.orders.repository import OrdersUnit

_C = CaptureRow


def _capture(row: RowMapping) -> Capture:
    return Capture(
        id=row["id"],
        order_id=row["order_id"],
        job_status=row["job_status"],
        job_error=row["job_error"],
        item_count=row["item_count"],
        image_path=row["image_path"],
        warnings=row["warnings"],
    )


class CapturesUnit:
    def __init__(self, conn: AsyncConnection):
        self.conn = conn
        self.orders = OrdersUnit(conn)

    async def capture(self, capture_id: int) -> Capture | None:
        row = (await self.conn.execute(select(_C).where(_C.id == capture_id))).mappings().first()
        return _capture(row) if row else None

    async def create(self, order_id: int, image_path: str) -> int:
        created = await self.conn.execute(insert(_C).values(order_id=order_id, image_path=image_path).returning(_C.id))
        return int(created.scalar_one())

    async def update(self, capture_id: int, only_if_status: tuple[str, ...] = (), **values: Any) -> None:
        """`only_if_status`: leave the capture alone unless its job is in one of these states."""
        query = update(_C).where(_C.id == capture_id)
        if only_if_status:
            query = query.where(_C.job_status.in_(only_if_status))
        await self.conn.execute(query.values(**values))


class CapturesRepository:
    def __init__(self, engine: AsyncEngine):
        self.engine = engine

    @asynccontextmanager
    async def read(self) -> AsyncIterator[CapturesUnit]:
        async with self.engine.connect() as conn:
            yield CapturesUnit(conn)

    @asynccontextmanager
    async def write(self) -> AsyncIterator[CapturesUnit]:
        """One transaction: committed when the block ends, rolled back when it raises."""
        async with self.engine.begin() as conn:
            yield CapturesUnit(conn)
