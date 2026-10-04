"""Orders: one open order per cashier at a time, lines merged per product, prices frozen at checkout."""

from app.core.clock import local_midnight_utc
from app.core.config import CoreSettings
from app.core.db import iso
from app.core.errors import AppError, Conflict, Invalid, NotFound
from app.core.signed_url import signed_query
from app.modules.auth.ports import CurrentUser
from app.modules.auth.service import Forbidden
from app.modules.catalog.service import CatalogService
from app.modules.orders.ports import Order
from app.modules.orders.repository import OrdersRepository, OrdersUnit
from app.modules.orders.schemas import (
    MAX_QTY,
    CheckoutIn,
    HistoryItemOut,
    ItemIn,
    ItemPatch,
    OrderItemOut,
    OrderOut,
)
from app.modules.pos_settings.service import SettingsService

HISTORY_LIMIT = 200
# range name -> how many days before today it starts
_RANGE_DAYS = {"today": 0, "7d": 6, "30d": 29}


class OrdersService:
    def __init__(
        self, repo: OrdersRepository, catalog: CatalogService, pos_settings: SettingsService, settings: CoreSettings
    ):
        self.repo, self.catalog, self.pos_settings, self.settings = repo, catalog, pos_settings, settings

    # ---------------------------------------------------------------- reading

    def _thumbnail_url(self, rel_path: str | None) -> str | None:
        if not rel_path:
            return None
        query = signed_query(self.settings.media_url_secret, rel_path, self.settings.media_url_ttl_seconds)
        return f"/api/media/{rel_path}?{query}"

    async def _view(self, unit: OrdersUnit, order: Order) -> OrderOut:
        """The order as the POS shows it. An open order is priced from the catalog as it is now;
        a paid one from the prices frozen at checkout."""
        paid = order.status == "paid"
        lines = await unit.items(order.id)
        products = await self.catalog.lookup({line.product_id for line in lines})
        items, total = [], 0
        for line in lines:
            product = products.get(line.product_id)
            if line.manual_price is not None:
                price: int | None = line.manual_price
            else:
                price = line.unit_price if paid else (product.price if product else None)
            line_total = None if price is None else price * line.quantity
            total += line_total or 0
            items.append(
                OrderItemOut(
                    id=line.id,
                    product_id=line.product_id,
                    product_name=product.name if product else f"SKU {line.product_id}",
                    quantity=line.quantity,
                    unit_price=price,
                    price_missing=price is None,
                    manual_price=line.manual_price is not None,
                    line_total=line_total,
                    flagged=line.flagged,
                    thumbnail_url=self._thumbnail_url(line.thumb_path),
                    evidence=line.evidence,
                )
            )
        return OrderOut(
            id=order.id,
            status=order.status,
            created_at=iso(order.created_at),
            paid_at=iso(order.paid_at) if order.paid_at else None,
            payment_method=order.payment_method,
            cash_given=order.cash_given,
            change_given=order.change_given,
            items=items,
            captures=[],
            item_count=sum(item.quantity for item in items),
            total=(order.total_amount or 0) if paid else total,
            missing_price_count=sum(1 for item in items if item.price_missing),
            flagged_count=sum(1 for item in items if item.flagged),
        )

    @staticmethod
    async def _visible(unit: OrdersUnit, current: CurrentUser, order_id: int, lock: bool = False) -> Order:
        order = await unit.order(order_id, lock=lock)
        if order is None or (not current.is_admin and order.cashier_id != current.user_id):
            # the same answer for someone else's order: it does not reveal that the order exists
            raise NotFound("Không tìm thấy đơn hàng")
        return order

    async def _open(self, unit: OrdersUnit, current: CurrentUser, order_id: int) -> Order:
        """The order, locked for a change. 409 when it is already paid or voided."""
        order = await self._visible(unit, current, order_id, lock=True)
        if order.status != "open":
            raise Conflict("Đơn hàng đã thanh toán hoặc đã huỷ", code="ORDER_NOT_OPEN")
        return order

    async def get(self, current: CurrentUser, order_id: int) -> OrderOut:
        async with self.repo.read() as unit:
            return await self._view(unit, await self._visible(unit, current, order_id))

    async def open_order(self, current: CurrentUser) -> OrderOut | None:
        """The cashier's latest open order, to resume after a page reload or a new login."""
        async with self.repo.read() as unit:
            order = await unit.latest_open_order(current.user_id)
            return await self._view(unit, order) if order else None

    async def history(
        self, current: CurrentUser, range_name: str = "today", everyone: bool = False
    ) -> list[HistoryItemOut]:
        if range_name == "all":
            since = None
        elif range_name in _RANGE_DAYS:
            since = local_midnight_utc(self.settings.timezone_offset_hours, _RANGE_DAYS[range_name])
        else:
            raise Invalid("range phải là today|7d|30d|all")
        async with self.repo.read() as unit:
            entries = await unit.history(None if everyone else current.user_id, since, HISTORY_LIMIT)
        return [
            HistoryItemOut(
                id=entry.order.id,
                status=entry.order.status,
                created_at=iso(entry.order.created_at),
                paid_at=iso(entry.order.paid_at) if entry.order.paid_at else None,
                total=entry.order.total_amount or 0,
                item_count=entry.item_count,
                payment_method=entry.order.payment_method,
                cashier=entry.cashier_name,
            )
            for entry in entries
        ]

    # ---------------------------------------------------------------- changing

    async def create(self, current: CurrentUser) -> OrderOut:
        """A new order, or the cashier's open order that is still empty (moved to the current
        shift), so reloading the page does not leave orphan orders behind."""
        async with self.repo.write() as unit:
            order_id = await unit.empty_open_order_id(current.user_id)
            if order_id is None:
                order_id = await unit.create_order(current.user_id, current.shift_id)
            else:
                await unit.update_order(order_id, shift_id=current.shift_id)
        return await self.get(current, order_id)

    async def _known_product(self, product_id: str) -> str:
        product_id = product_id.strip()
        if product_id not in await self.catalog.lookup([product_id]):
            raise Invalid("Sản phẩm không tồn tại")
        return product_id

    async def add_item(self, current: CurrentUser, order_id: int, body: ItemIn) -> OrderOut:
        product_id = await self._known_product(body.product_id)
        async with self.repo.write() as unit:
            await self._open(unit, current, order_id)
            await self._merge_or_insert(unit, order_id, product_id, body.quantity)
        return await self.get(current, order_id)

    @staticmethod
    async def _merge_or_insert(unit: OrdersUnit, order_id: int, product_id: str, quantity: int) -> int:
        """More of a product goes onto its confirmed line; otherwise a new line. Returns the line id."""
        line = await unit.mergeable_item(order_id, product_id)
        if line is None:
            return await unit.insert_item(order_id, product_id, quantity)
        await unit.update_item(line.id, quantity=min(MAX_QTY, line.quantity + quantity))
        return line.id

    async def update_item(self, current: CurrentUser, order_id: int, item_id: int, body: ItemPatch) -> OrderOut:
        sent = body.model_fields_set
        if "quantity" in sent and body.quantity is None:
            raise Invalid("'quantity' phải là số nguyên")
        product_id = await self._known_product(body.product_id) if body.product_id is not None else None
        async with self.repo.write() as unit:
            await self._open(unit, current, order_id)
            line = await unit.item(order_id, item_id)
            if line is None:
                raise NotFound("Không thấy dòng hàng")
            quantity = body.quantity if body.quantity is not None else line.quantity
            if "quantity" in sent:
                await unit.update_item(item_id, quantity=quantity)
            if "manual_price" in sent:
                await unit.update_item(item_id, manual_price=body.manual_price)
            if body.confirm:
                await unit.update_item(item_id, flagged=False)
            if product_id is not None:
                # the cashier says what the product really is: the line is confirmed, and what the
                # recognizer found for the other product no longer applies
                await unit.update_item(item_id, product_id=product_id, flagged=False, manual_price=None, evidence=None)
                other = await unit.mergeable_item(order_id, product_id, other_than=item_id)
                if other is not None:
                    await unit.update_item(other.id, quantity=min(MAX_QTY, other.quantity + quantity))
                    await unit.delete_item(order_id, item_id)
        return await self.get(current, order_id)

    async def delete_item(self, current: CurrentUser, order_id: int, item_id: int) -> OrderOut:
        async with self.repo.write() as unit:
            await self._open(unit, current, order_id)
            if not await unit.delete_item(order_id, item_id):
                raise NotFound("Không thấy dòng hàng")
        return await self.get(current, order_id)

    async def checkout(self, current: CurrentUser, order_id: int, body: CheckoutIn) -> OrderOut:
        allow_missing = (await self.pos_settings.public()).allow_checkout_without_price
        async with self.repo.write() as unit:
            order = await self._open(unit, current, order_id)
            view = await self._view(unit, order)
            if not view.items:
                raise Invalid("Đơn hàng chưa có sản phẩm")
            if view.missing_price_count and not allow_missing:
                raise AppError(
                    "Còn sản phẩm chưa có giá, hãy nhập giá tay",
                    code="PRICE_MISSING_BLOCKED",
                    status_code=409,
                    missing=view.missing_price_count,
                )
            total = view.total  # a line without a price counts as 0 when that is allowed
            cash_given = change = None
            if body.method == "cash":
                if body.cash_given is None:
                    raise Invalid("'cash_given' phải là số nguyên")
                if body.cash_given < total:
                    raise Invalid("Tiền khách đưa chưa đủ")
                cash_given, change = body.cash_given, body.cash_given - total
            for item in view.items:  # freeze the unit prices: a later price change leaves this receipt alone
                await unit.update_item(item.id, unit_price=item.unit_price or 0)
            await unit.mark_paid(order_id, body.method, cash_given, change, total)
            await unit.add_to_shift(current.shift_id, total)
        return await self.get(current, order_id)

    async def void(self, current: CurrentUser, order_id: int) -> OrderOut:
        """Cancel an order. A cashier may cancel an open one; a paid one only an admin, and its
        amount is taken back from the shift that collected it. Idempotent."""
        async with self.repo.write() as unit:
            order = await self._visible(unit, current, order_id, lock=True)
            if order.status == "paid" and not current.is_admin:
                raise Forbidden("Chỉ quản trị viên được huỷ đơn đã thanh toán")
            if order.status != "void":
                if order.status == "paid" and order.shift_id is not None:
                    await unit.add_to_shift(order.shift_id, -(order.total_amount or 0))
                await unit.update_order(order_id, status="void")
        return await self.get(current, order_id)
