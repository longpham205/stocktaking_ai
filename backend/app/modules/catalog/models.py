"""Prices. The product catalog itself (names, barcodes, recognition evidence) is the engine's
tables; the price is a business fact the web owns."""

from datetime import datetime

from sqlalchemy import CheckConstraint, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base
from app.core.columns import MONEY, created_at_column


class ProductPriceRow(Base):
    __tablename__ = "product_prices"
    __table_args__ = (CheckConstraint("price >= 0", name="price_not_negative"),)

    product_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    price: Mapped[int] = mapped_column(MONEY)
    updated_at: Mapped[datetime] = created_at_column()
