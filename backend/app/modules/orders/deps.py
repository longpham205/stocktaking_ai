"""FastAPI dependency for the orders service."""

from fastapi import Depends

from app.core.backends import Backends
from app.core.deps import backends, require
from app.modules.orders.service import OrdersService


def orders_service(b: Backends = Depends(backends)) -> OrdersService:
    return require(b.orders, "Đơn hàng")
