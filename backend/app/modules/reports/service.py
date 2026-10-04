"""The admin dashboard: today's figures, revenue per day over a range, best sellers. Days are the
shop's (`TIMEZONE_OFFSET_HOURS`), not UTC's."""

from datetime import UTC, datetime, timedelta

from app.core.clock import local_date, local_midnight_utc
from app.core.errors import Invalid
from app.modules.catalog.service import CatalogService
from app.modules.recognition.worker import RecognitionWorker
from app.modules.reports.repository import ReportsRepository
from app.modules.reports.schemas import DayOut, ReportOut, TopProductOut

# range -> how many days it covers, today included
RANGE_DAYS = {"today": 1, "7d": 7, "30d": 30}


class ReportsService:
    def __init__(
        self, repo: ReportsRepository, catalog: CatalogService, worker: RecognitionWorker, timezone_offset_hours: float
    ):
        self.repo, self.catalog, self.worker, self.offset = repo, catalog, worker, timezone_offset_hours

    async def report(self, range_name: str = "today") -> ReportOut:
        if range_name not in RANGE_DAYS:
            raise Invalid("range phải là today|7d|30d")
        days = RANGE_DAYS[range_name]
        now = datetime.now(UTC)
        today_start = local_midnight_utc(self.offset, now=now)
        range_start = local_midnight_utc(self.offset, days - 1, now=now)

        today = await self.repo.today(today_start)
        local_today = (now + timedelta(hours=self.offset)).date()
        daily = {(local_today - timedelta(days=k)).isoformat(): [0, 0] for k in range(days - 1, -1, -1)}
        for sale in await self.repo.sales(range_start):
            bucket = daily.get(local_date(sale.paid_at, self.offset))
            if bucket is not None:
                bucket[0] += 1
                bucket[1] += sale.total
        tops = await self.repo.top_products(range_start)
        names = await self.catalog.lookup({t.product_id for t in tops})
        catalog = await self.catalog.admin_list(size=1)  # only its counters
        return ReportOut(
            range=range_name,
            range_orders=sum(v[0] for v in daily.values()),
            range_revenue=sum(v[1] for v in daily.values()),
            daily=[DayOut(date=day, orders=v[0], revenue=v[1]) for day, v in daily.items()],
            top_products=[
                TopProductOut(
                    product_id=t.product_id,
                    name=names[t.product_id].name if t.product_id in names else f"SKU {t.product_id}",
                    quantity=t.quantity,
                    revenue=t.revenue,
                )
                for t in tops
            ],
            orders_today=today.orders,
            revenue_today=today.revenue,
            captures_today=today.captures,
            error_rate=round(today.capture_errors / today.captures, 4) if today.captures else 0.0,
            avg_processing_ms=None if today.avg_processing_ms is None else round(today.avg_processing_ms, 1),
            active_shifts=today.active_shifts,
            active_staff=today.active_staff,
            queue_size=self.worker.queued,
            products_total=catalog.total,
            products_missing_price=catalog.missing_price,
            products_missing_barcode=catalog.missing_barcode,
        )
