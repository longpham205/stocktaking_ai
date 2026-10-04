"""Postgres queries behind the admin reports. The only place in the module that runs SQL; it only
reads, the tables are the orders, captures and auth modules'."""

from datetime import datetime

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from app.modules.auth.models import ShiftRow, UserRow
from app.modules.captures.models import CaptureRow
from app.modules.orders.models import OrderItemRow, OrderRow
from app.modules.reports.ports import Sale, TodayCounts, TopProduct

_O, _I, _C = OrderRow, OrderItemRow, CaptureRow
TOP_LIMIT = 5


class ReportsRepository:
    def __init__(self, engine: AsyncEngine):
        self.engine = engine

    async def today(self, since: datetime) -> TodayCounts:
        """Orders paid and photos taken since `since` (the shop's midnight), and who is working."""
        orders = select(func.count(), func.coalesce(func.sum(_O.total_amount), 0)).where(
            _O.status == "paid", _O.created_at >= since
        )
        captures = select(
            func.count(),
            func.count().filter(_C.job_status == "error"),
            func.avg(case((_C.job_status == "done", _C.processing_time_ms))),
        ).where(_C.created_at >= since)
        async with self.engine.connect() as conn:
            paid, revenue = (await conn.execute(orders)).one()
            taken, errors, average = (await conn.execute(captures)).one()
            shifts = (await conn.execute(select(func.count()).where(ShiftRow.ended_at.is_(None)))).scalar_one()
            staff = (await conn.execute(select(func.count()).where(UserRow.is_active))).scalar_one()
        return TodayCounts(
            orders=paid,
            revenue=int(revenue),
            captures=taken,
            capture_errors=errors,
            avg_processing_ms=None if average is None else float(average),
            active_shifts=shifts,
            active_staff=staff,
        )

    async def sales(self, since: datetime) -> list[Sale]:
        """Orders paid since `since` (a voided order is no longer paid: it leaves the reports)."""
        query = select(_O.paid_at, _O.total_amount).where(_O.status == "paid", _O.paid_at >= since)
        async with self.engine.connect() as conn:
            rows = (await conn.execute(query)).all()
        return [Sale(paid_at=paid_at, total=total or 0) for paid_at, total in rows]

    async def top_products(self, since: datetime) -> list[TopProduct]:
        """Best sellers since `since`: most units first, then most revenue (at the frozen prices)."""
        quantity = func.sum(_I.quantity)
        revenue = func.sum(_I.quantity * func.coalesce(_I.unit_price, 0))
        query = (
            select(_I.product_id, quantity, revenue)
            .join(_O, _O.id == _I.order_id)
            .where(_O.status == "paid", _O.paid_at >= since)
            .group_by(_I.product_id)
            .order_by(quantity.desc(), revenue.desc(), _I.product_id)
            .limit(TOP_LIMIT)
        )
        async with self.engine.connect() as conn:
            rows = (await conn.execute(query)).all()
        return [TopProduct(product_id=pid, quantity=int(q), revenue=int(r)) for pid, q, r in rows]
