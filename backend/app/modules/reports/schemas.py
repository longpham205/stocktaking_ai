"""Response body of the reports route (field names are the legacy API's)."""

from pydantic import BaseModel


class DayOut(BaseModel):
    # the shop's local date, YYYY-MM-DD
    date: str
    orders: int
    revenue: int


class TopProductOut(BaseModel):
    product_id: str
    name: str
    quantity: int
    revenue: int


class ReportOut(BaseModel):
    # the chosen range: today | 7d | 30d
    range: str
    range_orders: int
    range_revenue: int
    # one entry per day of the range, days without a sale included (a continuous chart)
    daily: list[DayOut]
    top_products: list[TopProductOut]
    # today, whatever the range
    orders_today: int
    revenue_today: int
    captures_today: int
    error_rate: float
    avg_processing_ms: float | None
    active_shifts: int
    active_staff: int
    queue_size: int
    products_total: int
    products_missing_price: int
    products_missing_barcode: int
