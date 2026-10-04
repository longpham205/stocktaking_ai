"""Orders: lines, prices, checkout, void, history. Ported from the legacy suite
(src_legacy/tests/test_backend_api.py); the lines a capture would add are inserted directly."""

from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import insert, select
from sqlalchemy.dialects.postgresql import insert as upsert
from sqlmodel import Session

from app.modules.auth.models import ShiftRow
from app.modules.auth.passwords import hash_password
from app.modules.catalog.models import ProductPriceRow
from app.modules.orders.models import OrderItemRow
from app.modules.pos_settings.models import SettingRow
from engine.catalog.db import Product, make_engine_from_url
from tests.api.conftest import ADMIN_PASSWORD, STAFF_PASSWORD, http, token_for


@pytest.fixture
def catalog(catalog_url: str) -> None:
    engine = make_engine_from_url(catalog_url)
    with Session(engine) as session:
        session.add(Product(product_id="7", product_name="Kem dưỡng da"))
        session.add(Product(product_id="8", product_name="Sữa rửa mặt"))
        session.add(Product(product_id="12", product_name="Son dưỡng", barcode="8931000009999"))
        session.add(Product(product_id="13", product_name="Phấn phủ (ngừng bán)", is_active=False))
        session.commit()
    engine.dispose()


@pytest.fixture
async def staff(db_app: FastAPI, catalog: None) -> Any:
    async with http(db_app, await token_for(db_app, "staff", STAFF_PASSWORD)) as client:
        yield client


@pytest.fixture
async def admin(db_app: FastAPI, catalog: None) -> Any:
    async with http(db_app, await token_for(db_app, "admin", ADMIN_PASSWORD)) as client:
        yield client


async def _set_price(app: FastAPI, product_id: str, price: int) -> None:
    statement = upsert(ProductPriceRow).values(product_id=product_id, price=price)
    statement = statement.on_conflict_do_update(index_elements=["product_id"], set_={"price": price})
    async with app.state.backends.engine.begin() as conn:
        await conn.execute(statement)


async def _order_with_items(app: FastAPI, client: httpx.AsyncClient) -> tuple[int, dict[str, Any]]:
    """What one basket photo leaves behind: 7 x2 and 8 x1 confirmed, one more 7 flagged."""
    order_id = (await client.post("/api/orders")).json()["id"]
    await client.post(f"/api/orders/{order_id}/items", json={"product_id": "7", "quantity": 2})
    await client.post(f"/api/orders/{order_id}/items", json={"product_id": "8"})
    async with app.state.backends.engine.begin() as conn:
        await conn.execute(
            insert(OrderItemRow).values(
                order_id=order_id, product_id="7", quantity=1, flagged=True, evidence={"similarity": 0.61}
            )
        )
    return order_id, (await client.get(f"/api/orders/{order_id}")).json()


async def test_orders_need_a_login(db_app: FastAPI) -> None:
    async with http(db_app) as anonymous:
        for response in (await anonymous.post("/api/orders"), await anonymous.get("/api/history")):
            assert response.status_code == 401 and response.json()["code"] == "AUTH_INVALID"


async def test_new_order_view_and_manual_add_merges_on_the_confirmed_line(staff: httpx.AsyncClient) -> None:
    order = (await staff.post("/api/orders")).json()
    assert order["status"] == "open" and order["items"] == [] and order["captures"] == []
    assert order["total"] == 0 and order["item_count"] == 0 and order["created_at"].endswith("+00:00")
    oid = order["id"]
    await staff.post(f"/api/orders/{oid}/items", json={"product_id": "12", "quantity": 2})
    response = await staff.post(f"/api/orders/{oid}/items", json={"product_id": " 12 "})
    assert response.status_code == 200
    (line,) = response.json()["items"]
    assert line == {
        "id": line["id"],
        "product_id": "12",
        "product_name": "Son dưỡng",
        "quantity": 3,
        "unit_price": None,
        "price_missing": True,
        "manual_price": False,
        "line_total": None,
        "flagged": False,
        "thumbnail_url": None,
        "evidence": None,
    }
    for bad in ({"product_id": "nope"}, {"product_id": "12", "quantity": 0}, {"product_id": "12", "quantity": "2"}):
        response = await staff.post(f"/api/orders/{oid}/items", json=bad)
        assert response.status_code == 422 and response.json()["code"] == "VALIDATION_ERROR"
    assert (await staff.post("/api/orders/999999/items", json={"product_id": "12"})).status_code == 404


async def test_edit_items_confirm_change_product_and_merge(db_app: FastAPI, staff: httpx.AsyncClient) -> None:
    oid, order = await _order_with_items(db_app, staff)
    assert order["flagged_count"] == 1 and order["item_count"] == 4
    flagged = next(i for i in order["items"] if i["flagged"])
    assert flagged["evidence"] == {"similarity": 0.61}
    # the cashier says the flagged line is product 8: it joins the confirmed line of 8
    response = await staff.patch(f"/api/orders/{oid}/items/{flagged['id']}", json={"product_id": "8"})
    assert response.status_code == 200
    order = response.json()
    assert {(i["product_id"], i["flagged"]): i["quantity"] for i in order["items"]} == {
        ("7", False): 2,
        ("8", False): 2,
    }
    line7 = next(i for i in order["items"] if i["product_id"] == "7")
    url = f"/api/orders/{oid}/items/{line7['id']}"
    for bad in ({"quantity": 0}, {"quantity": 5000}, {"quantity": "3"}, {"quantity": None}, {"product_id": "999"}):
        assert (await staff.patch(url, json=bad)).status_code == 422
    order = (await staff.patch(url, json={"quantity": 3})).json()
    assert next(i for i in order["items"] if i["id"] == line7["id"])["quantity"] == 3
    assert (await staff.delete(url)).status_code == 200
    assert (await staff.delete(url)).status_code == 404
    assert (await staff.patch(url, json={"quantity": 1})).status_code == 404


async def test_confirm_a_flagged_line_without_changing_the_product(db_app: FastAPI, staff: httpx.AsyncClient) -> None:
    oid, order = await _order_with_items(db_app, staff)
    flagged = next(i for i in order["items"] if i["flagged"])
    order = (await staff.patch(f"/api/orders/{oid}/items/{flagged['id']}", json={"confirm": True})).json()
    assert order["flagged_count"] == 0 and len(order["items"]) == 3  # confirmed in place, not merged


async def test_checkout_blocked_without_price_then_manual_price_then_pay(
    db_app: FastAPI, staff: httpx.AsyncClient, admin: httpx.AsyncClient
) -> None:
    oid, order = await _order_with_items(db_app, staff)
    assert all(i["price_missing"] for i in order["items"])
    blocked = await staff.post(f"/api/orders/{oid}/checkout", json={"method": "cash", "cash_given": 999999})
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "PRICE_MISSING_BLOCKED" and blocked.json()["missing"] == 3
    await _set_price(db_app, "7", 10000)
    await _set_price(db_app, "8", 25000)
    order = (await staff.get(f"/api/orders/{oid}")).json()
    assert not any(i["price_missing"] for i in order["items"])  # the flagged line of 7 is priced too
    # a product without a price: the cashier types one in
    order = (await staff.post(f"/api/orders/{oid}/items", json={"product_id": "12"})).json()
    assert order["missing_price_count"] == 1
    iid = next(i["id"] for i in order["items"] if i["product_id"] == "12")
    order = (await staff.patch(f"/api/orders/{oid}/items/{iid}", json={"manual_price": 5000})).json()
    assert order["missing_price_count"] == 0
    assert order["total"] == 10000 * 2 + 25000 + 10000 + 5000  # 7 x2 + 8 + 7 (flagged) + 12
    assert next(i for i in order["items"] if i["id"] == iid)["manual_price"] is True

    checkout = f"/api/orders/{oid}/checkout"
    for bad in ({"method": "cash", "cash_given": 1000}, {"method": "cash"}, {"method": "bitcoin"}, {}):
        assert (await staff.post(checkout, json=bad)).status_code == 422
    response = await staff.post(checkout, json={"method": "cash", "cash_given": 100000})
    paid = response.json()
    assert response.status_code == 200 and paid["status"] == "paid" and paid["paid_at"]
    assert paid["total"] == 60000 and paid["cash_given"] == 100000 and paid["change_given"] == 40000
    assert (await staff.get("/api/me")).json()["shift"]["total_collected"] == 60000

    # the prices are frozen: a later price change leaves the receipt alone
    await _set_price(db_app, "7", 99999)
    assert (await staff.get(f"/api/orders/{oid}")).json()["total"] == 60000
    for response in (
        await staff.patch(f"/api/orders/{oid}/items/{iid}", json={"quantity": 2}),
        await staff.post(f"/api/orders/{oid}/items", json={"product_id": "12"}),
        await staff.delete(f"/api/orders/{oid}/items/{iid}"),
        await staff.post(checkout, json={"method": "qr"}),
    ):
        assert response.status_code == 409 and response.json()["code"] == "ORDER_NOT_OPEN"

    # a paid order is voided by an admin only, and its amount leaves the cashier's shift
    refused = await staff.post(f"/api/orders/{oid}/void")
    assert refused.status_code == 403 and refused.json()["code"] == "FORBIDDEN"
    assert (await admin.post(f"/api/orders/{oid}/void")).json()["status"] == "void"
    assert (await admin.post(f"/api/orders/{oid}/void")).json()["status"] == "void"  # again: still fine
    async with db_app.state.backends.engine.connect() as conn:
        assert (await conn.execute(select(ShiftRow.total_collected))).scalars().all() == [0, 0]


async def test_allow_checkout_without_price_counts_missing_lines_as_zero(
    db_app: FastAPI, staff: httpx.AsyncClient
) -> None:
    oid, _ = await _order_with_items(db_app, staff)
    async with db_app.state.backends.engine.begin() as conn:
        await conn.execute(insert(SettingRow).values(key="allow_checkout_without_price", value=True))
    response = await staff.post(f"/api/orders/{oid}/checkout", json={"method": "qr"})
    assert response.status_code == 200 and response.json()["total"] == 0
    assert [i["unit_price"] for i in response.json()["items"]] == [0, 0, 0]


async def test_checkout_of_an_empty_order_is_rejected_and_qr_has_no_change(
    db_app: FastAPI, staff: httpx.AsyncClient
) -> None:
    oid = (await staff.post("/api/orders")).json()["id"]
    assert (await staff.post(f"/api/orders/{oid}/checkout", json={"method": "qr"})).status_code == 422
    await _set_price(db_app, "12", 7000)
    await staff.post(f"/api/orders/{oid}/items", json={"product_id": "12"})
    response = await staff.post(f"/api/orders/{oid}/checkout", json={"method": "qr"})
    paid = response.json()
    assert response.status_code == 200 and paid["payment_method"] == "qr"
    assert paid["cash_given"] is None and paid["change_given"] is None


async def test_history_and_void_of_an_open_order(
    db_app: FastAPI, staff: httpx.AsyncClient, admin: httpx.AsyncClient
) -> None:
    await _set_price(db_app, "12", 7000)
    first = (await staff.post("/api/orders")).json()["id"]
    await staff.post(f"/api/orders/{first}/items", json={"product_id": "12"})
    await staff.post(f"/api/orders/{first}/checkout", json={"method": "qr"})
    second = (await staff.post("/api/orders")).json()["id"]
    assert (await staff.post(f"/api/orders/{second}/void")).json()["status"] == "void"
    await staff.post("/api/orders")  # still open: not history yet

    response = await staff.get("/api/history?range=today")
    items = response.json()["items"]
    assert response.status_code == 200 and [x["id"] for x in items] == [second, first]
    assert items[1] == {
        "id": first,
        "status": "paid",
        "created_at": items[1]["created_at"],
        "paid_at": items[1]["paid_at"],
        "total": 7000,
        "item_count": 1,
        "payment_method": "qr",
        "cashier": "Thu ngân demo",
    }
    assert items[0]["total"] == 0 and items[0]["paid_at"] is None
    for name in ("7d", "30d", "all"):
        assert len((await staff.get(f"/api/history?range={name}")).json()["items"]) == 2
    assert (await staff.get("/api/history?range=lung-tung")).status_code == 422

    # history is the caller's own; an admin sees everyone's through /admin/orders
    assert (await admin.get("/api/history")).json()["items"] == []
    assert [x["id"] for x in (await admin.get("/api/admin/orders")).json()["items"]] == [second, first]
    # a cashier without a full name appears under the username
    await db_app.state.backends.auth.repo.create_user("khongten", hash_password("password222"), "staff", "")
    async with http(db_app, await token_for(db_app, "khongten", "password222")) as nameless:
        voided = (await nameless.post("/api/orders")).json()["id"]
        await nameless.post(f"/api/orders/{voided}/void")
    assert (await admin.get("/api/admin/orders")).json()["items"][0]["cashier"] == "khongten"
    forbidden = await staff.get("/api/admin/orders")
    assert forbidden.status_code == 403 and forbidden.json()["code"] == "FORBIDDEN"


async def test_open_order_is_resumed_and_an_empty_one_is_reused(
    db_app: FastAPI, staff: httpx.AsyncClient, admin: httpx.AsyncClient
) -> None:
    assert (await staff.get("/api/orders/open")).json() == {"order": None}
    first = (await staff.post("/api/orders")).json()["id"]
    assert (await staff.post("/api/orders")).json()["id"] == first  # empty: reused, no orphan order
    await staff.post(f"/api/orders/{first}/items", json={"product_id": "12", "quantity": 2})
    current = (await staff.get("/api/orders/open")).json()["order"]
    assert current["id"] == first and current["item_count"] == 2
    second = (await staff.post("/api/orders")).json()["id"]  # the first has lines: a really new order
    assert second != first and (await staff.get("/api/orders/open")).json()["order"]["id"] == second

    # a new login (a new shift): the unfinished order is still there, and the empty one moves shifts
    async with http(db_app, await token_for(db_app, "staff", STAFF_PASSWORD)) as again:
        assert (await again.get("/api/orders/open")).json()["order"]["id"] == second
        assert (await again.post("/api/orders")).json()["id"] == second
        await _set_price(db_app, "12", 5000)
        await again.post(f"/api/orders/{second}/items", json={"product_id": "12"})
        await again.post(f"/api/orders/{second}/checkout", json={"method": "qr"})
        assert (await again.get("/api/me")).json()["shift"]["total_collected"] == 5000  # the new shift's
        # paid: no longer the open order; the older unfinished one is
        assert (await again.get("/api/orders/open")).json()["order"]["id"] == first
    # nobody else sees the cashier's open order
    assert (await admin.get("/api/orders/open")).json() == {"order": None}


async def test_another_cashiers_order_is_hidden_and_an_admin_sees_it(
    db_app: FastAPI, staff: httpx.AsyncClient, admin: httpx.AsyncClient
) -> None:
    oid, order = await _order_with_items(db_app, staff)
    await db_app.state.backends.auth.repo.create_user("staff2", hash_password("password222"), "staff", "Thu ngân 2")
    async with http(db_app, await token_for(db_app, "staff2", "password222")) as other:
        item = order["items"][0]["id"]
        for response in (
            await other.get(f"/api/orders/{oid}"),
            await other.post(f"/api/orders/{oid}/void"),
            await other.post(f"/api/orders/{oid}/items", json={"product_id": "12"}),
            await other.delete(f"/api/orders/{oid}/items/{item}"),
            await other.post(f"/api/orders/{oid}/checkout", json={"method": "qr"}),
        ):
            assert response.status_code == 404 and response.json()["code"] == "NOT_FOUND"
    assert (await admin.get(f"/api/orders/{oid}")).status_code == 200
    assert (await staff.get(f"/api/orders/{oid}")).json()["status"] == "open"


async def test_a_line_of_a_product_no_longer_on_sale_keeps_its_name(staff: httpx.AsyncClient) -> None:
    oid = (await staff.post("/api/orders")).json()["id"]
    order = (await staff.post(f"/api/orders/{oid}/items", json={"product_id": "13"})).json()
    assert order["items"][0]["product_name"] == "Phấn phủ (ngừng bán)"
