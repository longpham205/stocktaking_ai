"""Stock on hand: the admin's count, what paying and voiding an order do to it, the random seed."""

import asyncio
import random
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import insert, select
from sqlmodel import Session

from app.modules.auth.passwords import hash_password
from app.modules.catalog.models import ProductPriceRow
from app.modules.inventory.models import ProductStockRow
from app.modules.orders.models import OrderItemRow
from engine.catalog.db import Product, make_engine_from_url
from entrypoints.seed_stock import seed_stock
from tests.api.conftest import ADMIN_PASSWORD, STAFF_PASSWORD, http, token_for


@pytest.fixture
def catalog(catalog_url: str) -> None:
    engine = make_engine_from_url(catalog_url)
    with Session(engine) as session:
        session.add(Product(product_id="7", product_name="Kem dưỡng da"))
        session.add(Product(product_id="8", product_name="Sữa rửa mặt"))
        session.add(Product(product_id="12", product_name="Son dưỡng"))
        session.add(Product(product_id="13", product_name="Phấn phủ (ngừng bán)", is_active=False))
        session.commit()
    engine.dispose()


@pytest.fixture
async def priced(db_app: FastAPI, catalog: None) -> None:
    async with db_app.state.backends.engine.begin() as conn:
        await conn.execute(insert(ProductPriceRow), [{"product_id": pid, "price": 1000} for pid in ("7", "8", "12")])


@pytest.fixture
async def staff(db_app: FastAPI, priced: None) -> Any:
    async with http(db_app, await token_for(db_app, "staff", STAFF_PASSWORD)) as client:
        yield client


@pytest.fixture
async def admin(db_app: FastAPI, priced: None) -> Any:
    async with http(db_app, await token_for(db_app, "admin", ADMIN_PASSWORD)) as client:
        yield client


async def _stock(app: FastAPI) -> dict[str, int]:
    async with app.state.backends.engine.connect() as conn:
        rows = (await conn.execute(select(ProductStockRow.product_id, ProductStockRow.quantity))).all()
    return {product_id: quantity for product_id, quantity in rows}


async def _paid_order(client: httpx.AsyncClient, lines: list[tuple[str, int]]) -> int:
    order_id: int = (await client.post("/api/orders")).json()["id"]
    for product_id, quantity in lines:
        added = await client.post(
            f"/api/orders/{order_id}/items", json={"product_id": product_id, "quantity": quantity}
        )
        assert added.status_code == 200, added.text
    paid = await client.post(f"/api/orders/{order_id}/checkout", json={"method": "qr"})
    assert paid.status_code == 200, paid.text
    return order_id


async def test_admin_counts_stock_with_validation_filter_and_log(
    db_app: FastAPI, admin: httpx.AsyncClient, staff: httpx.AsyncClient
) -> None:
    listed = (await admin.get("/api/admin/products")).json()
    assert [p["stock"] for p in listed["items"]] == [None, None, None] and listed["out_of_stock"] == 0

    response = await admin.patch("/api/admin/products/7", json={"stock": 40})
    assert response.status_code == 200 and response.json()["stock"] == 40
    await admin.patch("/api/admin/products/7", json={"stock": 40})  # unchanged: not logged
    await admin.patch("/api/admin/products/8", json={"stock": 0})
    assert (await staff.get("/api/catalog/products?search=kem")).json()["items"][0]["stock"] == 40
    for bad in ({"stock": -1}, {"stock": 1.5}, {"stock": "3"}, {"stock": 1_000_001}):
        refused = await admin.patch("/api/admin/products/7", json=bad)
        assert refused.status_code == 422 and refused.json()["code"] == "VALIDATION_ERROR", bad
    assert (await staff.patch("/api/admin/products/7", json={"stock": 1})).status_code == 403

    listed = (await admin.get("/api/admin/products")).json()
    assert listed["out_of_stock"] == 1  # counted at 0; a product nobody counted is not "out"
    out = (await admin.get("/api/admin/products?filter=out_of_stock")).json()
    assert [p["id"] for p in out["items"]] == ["8"]

    assert (await admin.patch("/api/admin/products/7", json={"stock": None})).json()["stock"] is None
    assert await _stock(db_app) == {"8": 0}  # not tracked any more: the row is gone
    entries = (await admin.get("/api/admin/change-log?table=product&record=7")).json()["items"]
    assert [(e["field"], e["old"], e["new"]) for e in entries] == [("stock", "40", None), ("stock", None, "40")]


async def test_paying_takes_stock_off_and_voiding_a_paid_order_puts_it_back(
    db_app: FastAPI, admin: httpx.AsyncClient, staff: httpx.AsyncClient
) -> None:
    await admin.patch("/api/admin/products/7", json={"stock": 10})
    await admin.patch("/api/admin/products/8", json={"stock": 1})
    order_id: int = (await staff.post("/api/orders")).json()["id"]
    await staff.post(f"/api/orders/{order_id}/items", json={"product_id": "7", "quantity": 2})
    await staff.post(f"/api/orders/{order_id}/items", json={"product_id": "8", "quantity": 3})
    await staff.post(f"/api/orders/{order_id}/items", json={"product_id": "12", "quantity": 5})
    async with db_app.state.backends.engine.begin() as conn:  # a second line of 7, as a capture leaves one
        await conn.execute(insert(OrderItemRow).values(order_id=order_id, product_id="7", quantity=1, flagged=True))
    assert await _stock(db_app) == {"7": 10, "8": 1}  # an open order holds nothing

    paid = await staff.post(f"/api/orders/{order_id}/checkout", json={"method": "qr"})
    assert paid.status_code == 200
    # both lines of 7 count; 8 goes below zero instead of blocking the sale; 12 is not tracked
    assert await _stock(db_app) == {"7": 7, "8": -2}
    assert (await admin.get("/api/admin/products")).json()["out_of_stock"] == 1

    assert (await admin.post(f"/api/orders/{order_id}/void")).json()["status"] == "void"
    assert await _stock(db_app) == {"7": 10, "8": 1}
    await admin.post(f"/api/orders/{order_id}/void")  # again: nothing is put back twice
    assert await _stock(db_app) == {"7": 10, "8": 1}

    unpaid: int = (await staff.post("/api/orders")).json()["id"]
    await staff.post(f"/api/orders/{unpaid}/items", json={"product_id": "7", "quantity": 4})
    await staff.post(f"/api/orders/{unpaid}/void")  # never paid: nothing was taken off
    assert await _stock(db_app) == {"7": 10, "8": 1}


async def test_a_refused_checkout_leaves_the_stock_alone(
    db_app: FastAPI, admin: httpx.AsyncClient, staff: httpx.AsyncClient
) -> None:
    await admin.patch("/api/admin/products/7", json={"stock": 10})
    order_id: int = (await staff.post("/api/orders")).json()["id"]
    await staff.post(f"/api/orders/{order_id}/items", json={"product_id": "7", "quantity": 2})
    short = await staff.post(f"/api/orders/{order_id}/checkout", json={"method": "cash", "cash_given": 1})
    assert short.status_code == 422
    assert await _stock(db_app) == {"7": 10}


async def test_orders_paid_at_the_same_time_all_count(db_app: FastAPI, admin: httpx.AsyncClient) -> None:
    await admin.patch("/api/admin/products/7", json={"stock": 100})
    await admin.patch("/api/admin/products/8", json={"stock": 100})
    cashiers = []
    for index in range(4):
        name = f"quay{index}"
        await db_app.state.backends.auth.repo.create_user(name, hash_password("password222"), "staff", name)
        cashiers.append(http(db_app, await token_for(db_app, name, "password222")))
    try:
        # the two products in opposite orders: the rows are still locked in one order
        baskets = [[("7", 2), ("8", 1)], [("8", 1), ("7", 2)]] * 2
        await asyncio.gather(*(_paid_order(client, basket) for client, basket in zip(cashiers, baskets, strict=True)))
    finally:
        for client in cashiers:
            await client.aclose()
    assert await _stock(db_app) == {"7": 92, "8": 96}


async def test_a_count_is_reverted_only_while_no_sale_has_moved_it(
    admin: httpx.AsyncClient, staff: httpx.AsyncClient
) -> None:
    await admin.patch("/api/admin/products/7", json={"stock": 10})
    await admin.patch("/api/admin/products/7", json={"stock": 25})

    async def entries() -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = (await admin.get("/api/admin/change-log?table=product&record=7")).json()["items"]
        return items

    newest = (await entries())[0]
    assert (await admin.post(f"/api/admin/change-log/{newest['id']}/revert")).status_code == 200  # 25 -> 10
    assert (await admin.get("/api/admin/products?search=kem")).json()["items"][0]["stock"] == 10

    await admin.patch("/api/admin/products/7", json={"stock": 30})
    typed_30 = (await entries())[0]
    await _paid_order(staff, [("7", 1)])  # 29 now: putting 10 back would lose the sale
    stale = await admin.post(f"/api/admin/change-log/{typed_30['id']}/revert")
    assert stale.status_code == 409 and stale.json()["code"] == "CHANGE_STALE"


def test_seed_stock_fills_only_products_on_sale_without_a_quantity(catalog_url: str, database_url: str) -> None:
    engine = make_engine_from_url(catalog_url)
    with Session(engine) as session:
        session.add(Product(product_id="1", product_name="A"))
        session.add(Product(product_id="2", product_name="B"))
        session.add(Product(product_id="3", product_name="C", is_active=False))
        session.commit()
    with engine.begin() as conn:
        conn.execute(insert(ProductStockRow).values(product_id="2", quantity=3))
    assert seed_stock(catalog_url, 20, 100, random.Random(1)) == 1
    assert seed_stock(catalog_url, 20, 100, random.Random(2)) == 0  # again: nothing new
    with engine.connect() as conn:
        rows = dict(conn.execute(select(ProductStockRow.product_id, ProductStockRow.quantity)).tuples().all())
    engine.dispose()
    assert set(rows) == {"1", "2"} and rows["2"] == 3 and 20 <= rows["1"] <= 100
