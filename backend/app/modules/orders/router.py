"""`/api/orders*`, `/api/history` and `/api/admin/orders`. Every route that changes an order answers
with the order as it is afterwards."""

from fastapi import APIRouter, Depends

from app.modules.auth.deps import current_user, require_admin
from app.modules.auth.ports import CurrentUser
from app.modules.orders.deps import orders_service
from app.modules.orders.schemas import CheckoutIn, HistoryOut, ItemIn, ItemPatch, OpenOrderOut, OrderOut
from app.modules.orders.service import OrdersService

router = APIRouter(tags=["orders"])


@router.post("/orders")
async def create_order(
    current: CurrentUser = Depends(current_user), service: OrdersService = Depends(orders_service)
) -> OrderOut:
    return await service.create(current)


# before /orders/{order_id}: "open" is not an order id
@router.get("/orders/open")
async def open_order(
    current: CurrentUser = Depends(current_user), service: OrdersService = Depends(orders_service)
) -> OpenOrderOut:
    return OpenOrderOut(order=await service.open_order(current))


@router.get("/orders/{order_id}")
async def get_order(
    order_id: int, current: CurrentUser = Depends(current_user), service: OrdersService = Depends(orders_service)
) -> OrderOut:
    return await service.get(current, order_id)


@router.post("/orders/{order_id}/items")
async def add_item(
    order_id: int,
    body: ItemIn,
    current: CurrentUser = Depends(current_user),
    service: OrdersService = Depends(orders_service),
) -> OrderOut:
    return await service.add_item(current, order_id, body)


@router.patch("/orders/{order_id}/items/{item_id}")
async def update_item(
    order_id: int,
    item_id: int,
    body: ItemPatch,
    current: CurrentUser = Depends(current_user),
    service: OrdersService = Depends(orders_service),
) -> OrderOut:
    return await service.update_item(current, order_id, item_id, body)


@router.delete("/orders/{order_id}/items/{item_id}")
async def delete_item(
    order_id: int,
    item_id: int,
    current: CurrentUser = Depends(current_user),
    service: OrdersService = Depends(orders_service),
) -> OrderOut:
    return await service.delete_item(current, order_id, item_id)


@router.post("/orders/{order_id}/checkout")
async def checkout(
    order_id: int,
    body: CheckoutIn,
    current: CurrentUser = Depends(current_user),
    service: OrdersService = Depends(orders_service),
) -> OrderOut:
    return await service.checkout(current, order_id, body)


@router.post("/orders/{order_id}/void")
async def void_order(
    order_id: int, current: CurrentUser = Depends(current_user), service: OrdersService = Depends(orders_service)
) -> OrderOut:
    return await service.void(current, order_id)


@router.get("/history")
async def history(
    range: str = "today",  # noqa: A002  the legacy query parameter's name
    current: CurrentUser = Depends(current_user),
    service: OrdersService = Depends(orders_service),
) -> HistoryOut:
    return HistoryOut(items=await service.history(current, range))


@router.get("/admin/orders")
async def admin_orders(
    range: str = "today",  # noqa: A002
    admin: CurrentUser = Depends(require_admin),
    service: OrdersService = Depends(orders_service),
) -> HistoryOut:
    return HistoryOut(items=await service.history(admin, range, everyone=True))
