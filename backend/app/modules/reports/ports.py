"""What the reports are computed from: plain records, no SQLAlchemy rows."""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class TodayCounts:
    orders: int
    revenue: int
    captures: int
    capture_errors: int
    # of the captures recognised successfully; None when there were none
    avg_processing_ms: float | None
    active_shifts: int
    active_staff: int


@dataclass(frozen=True)
class Sale:
    paid_at: datetime
    total: int


@dataclass(frozen=True)
class TopProduct:
    product_id: str
    quantity: int
    revenue: int
