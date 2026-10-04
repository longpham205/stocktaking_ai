"""Orders and their lines.

An order belongs to the cashier, not to the shift: a cashier who logs in again resumes the open
order. `product_id` is the catalog's id (the benchmark's `category_id`); it is not a foreign key
because the catalog tables are the engine's, and a line must survive a product being deactivated.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, String, Text
from sqlalchemy import text as sql
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base
from app.core.columns import MONEY, TIMESTAMP, created_at_column, id_column


class OrderRow(Base):
    __tablename__ = "orders"
    __table_args__ = (
        CheckConstraint("status IN ('open', 'paid', 'void')", name="status"),
        # history of one cashier, newest first
        Index("ix_orders_cashier_id_created_at", "cashier_id", "created_at"),
    )

    id: Mapped[int] = id_column()
    shift_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("shifts.id"))
    cashier_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(16), server_default="open")
    payment_method: Mapped[str | None] = mapped_column(String(16))
    cash_given: Mapped[int | None] = mapped_column(MONEY)
    change_given: Mapped[int | None] = mapped_column(MONEY)
    total_amount: Mapped[int | None] = mapped_column(MONEY)
    created_at: Mapped[datetime] = created_at_column()
    paid_at: Mapped[datetime | None] = mapped_column(TIMESTAMP)


class OrderItemRow(Base):
    __tablename__ = "order_items"
    __table_args__ = (CheckConstraint("quantity >= 1", name="quantity_positive"),)

    id: Mapped[int] = id_column()
    order_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[str] = mapped_column(String(64))
    quantity: Mapped[int] = mapped_column(server_default="1")
    # the catalog price when the line was added; null = the product had no price
    unit_price: Mapped[int | None] = mapped_column(MONEY)
    # typed in by the cashier for a product without a price
    manual_price: Mapped[int | None] = mapped_column(MONEY)
    # recognised but not confidently: the cashier must confirm the line
    flagged: Mapped[bool] = mapped_column(server_default=sql("false"))
    thumb_path: Mapped[str | None] = mapped_column(Text)
    evidence: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = created_at_column()
