"""Stock on hand. Like the price, a business fact the web owns, next to the engine's catalog."""

from datetime import datetime

from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base
from app.core.columns import created_at_column


class ProductStockRow(Base):
    """A product without a row is not tracked: selling it changes nothing."""

    __tablename__ = "product_stock"

    product_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    # below 0 when more was sold than counted: the sale is never blocked, the admin screen shows it
    quantity: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[datetime] = created_at_column()
