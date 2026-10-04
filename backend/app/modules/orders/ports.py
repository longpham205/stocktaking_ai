"""What the rest of the app knows about orders: plain records, no SQLAlchemy rows."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class Order:
    id: int
    shift_id: int | None
    cashier_id: int
    # open | paid | void
    status: str
    payment_method: str | None
    cash_given: int | None
    change_given: int | None
    total_amount: int | None
    created_at: datetime
    paid_at: datetime | None


@dataclass(frozen=True)
class OrderItem:
    id: int
    order_id: int
    product_id: str
    quantity: int
    # frozen at checkout; null while the order is open
    unit_price: int | None
    manual_price: int | None
    flagged: bool
    thumb_path: str | None
    evidence: dict[str, Any] | None


@dataclass(frozen=True)
class HistoryEntry:
    order: Order
    cashier_name: str
    item_count: int
