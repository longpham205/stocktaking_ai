"""Request and response bodies of the order routes (field names are the legacy API's)."""

from typing import Any, Literal

from pydantic import BaseModel, Field, StrictBool, StrictInt

MAX_QTY = 999
MAX_PRICE = 100_000_000


class ItemIn(BaseModel):
    product_id: str = ""
    quantity: StrictInt = Field(1, ge=1, le=MAX_QTY)


class ItemPatch(BaseModel):
    """Only the fields sent are applied. `manual_price: null` removes the typed-in price."""

    quantity: StrictInt | None = Field(None, ge=1, le=MAX_QTY)
    manual_price: StrictInt | None = Field(None, ge=0, le=MAX_PRICE)
    # true: the cashier confirms a line the recognizer was not sure about
    confirm: StrictBool = False
    product_id: str | None = None


class CheckoutIn(BaseModel):
    method: Literal["cash", "qr"]
    cash_given: StrictInt | None = Field(None, ge=0, le=MAX_PRICE * 10)


class OrderItemOut(BaseModel):
    id: int
    product_id: str
    product_name: str
    quantity: int
    # null: the product has no price and none was typed in
    unit_price: int | None
    price_missing: bool
    # the unit price was typed in by the cashier
    manual_price: bool
    line_total: int | None
    flagged: bool
    thumbnail_url: str | None
    evidence: dict[str, Any] | None


class OrderOut(BaseModel):
    id: int
    status: str
    created_at: str
    paid_at: str | None
    payment_method: str | None
    cash_given: int | None
    change_given: int | None
    items: list[OrderItemOut]
    # the order's basket photos with their boxes (filled by the captures module)
    captures: list[dict[str, Any]]
    item_count: int
    total: int
    missing_price_count: int
    flagged_count: int


class OpenOrderOut(BaseModel):
    order: OrderOut | None


class HistoryItemOut(BaseModel):
    id: int
    status: str
    created_at: str
    paid_at: str | None
    total: int
    item_count: int
    payment_method: str | None
    cashier: str


class HistoryOut(BaseModel):
    items: list[HistoryItemOut]
