"""Staff management and the admin reports (3e-3). Ported from the legacy suite
(tests/test_backend_api.py of web v1, git commit f30710d)."""

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.dialects.postgresql import insert
from sqlmodel import Session

from app.modules.catalog.models import ProductPriceRow
from engine.catalog.db import Product, make_engine_from_url
from tests.api.conftest import ADMIN_PASSWORD, STAFF_PASSWORD, http, login, token_for


@pytest.fixture
def catalog(catalog_url: str) -> None:
    engine = make_engine_from_url(catalog_url)
    with Session(engine) as session:
        session.add(Product(product_id="7", product_name="Kem dưỡng da"))
        session.add(Product(product_id="8", product_name="Sữa rửa mặt", barcode="8930000000088"))
        session.add(Product(product_id="12", product_name="Son dưỡng"))
        session.commit()
    engine.dispose()


@pytest.fixture
async def admin(db_app: FastAPI, catalog: None) -> AsyncIterator[httpx.AsyncClient]:
    async with http(db_app, await token_for(db_app, "admin", ADMIN_PASSWORD)) as client:
        yield client


@pytest.fixture
async def staff(db_app: FastAPI, catalog: None) -> AsyncIterator[httpx.AsyncClient]:
    async with http(db_app, await token_for(db_app, "staff", STAFF_PASSWORD)) as client:
        yield client


async def _price(app: FastAPI, prices: dict[str, int]) -> None:
    async with app.state.backends.engine.begin() as conn:
        for pid, price in prices.items():
            statement = insert(ProductPriceRow).values(product_id=pid, price=price)
            await conn.execute(statement.on_conflict_do_update(index_elements=["product_id"], set_={"price": price}))


async def _pay(app: FastAPI, staff: httpx.AsyncClient, items: dict[str, int], prices: dict[str, int]) -> dict[str, Any]:
    await _price(app, prices)
    order_id = (await staff.post("/api/orders")).json()["id"]
    for pid, quantity in items.items():
        await staff.post(f"/api/orders/{order_id}/items", json={"product_id": pid, "quantity": quantity})
    response = await staff.post(f"/api/orders/{order_id}/checkout", json={"method": "qr"})
    assert response.status_code == 200, response.text
    paid: dict[str, Any] = response.json()
    return paid


# ---------------------------------------------------------------- staff


async def test_staff_list_create_validation_lock_and_password(db_app: FastAPI, admin: httpx.AsyncClient) -> None:
    listed = (await admin.get("/api/admin/users")).json()["items"]
    assert [(u["username"], u["role"], u["online"]) for u in listed] == [
        ("staff", "staff", False),
        ("admin", "admin", True),
    ]

    created = await admin.post(
        "/api/admin/users",
        json={"username": " Thu.Ngan1 ", "password": "matkhau123", "full_name": "Ngân", "role": "staff"},
    )
    assert created.status_code == 200
    member = created.json()
    assert member == {
        "id": member["id"],
        "username": "thu.ngan1",
        "full_name": "Ngân",
        "role": "staff",
        "is_active": True,
        "online": False,
    }
    taken = await admin.post("/api/admin/users", json={"username": "thu.ngan1", "password": "matkhau123"})
    assert taken.status_code == 409 and taken.json()["code"] == "USER_EXISTS"
    for bad in (
        {"username": "x", "password": "matkhau123"},
        {"username": "okuser", "password": "ngan"},
        {"username": "okuser", "password": "matkhau123", "role": "boss"},
    ):
        assert (await admin.post("/api/admin/users", json=bad)).status_code == 422, bad

    async with http(db_app, await token_for(db_app, "thu.ngan1", "matkhau123")) as cashier:
        assert (await admin.get("/api/admin/users")).json()["items"][-1]["online"] is True
        locked = await admin.patch(f"/api/admin/users/{member['id']}", json={"is_active": False})
        assert locked.status_code == 200 and locked.json()["is_active"] is False and locked.json()["online"] is False
        assert (await cashier.get("/api/me")).status_code == 401  # locked: logged out at once
    assert (await login(db_app, "thu.ngan1", "matkhau123")).status_code == 401
    await admin.patch(f"/api/admin/users/{member['id']}", json={"is_active": True})

    # a new password ends the open shift; the old password no longer works
    async with http(db_app, await token_for(db_app, "thu.ngan1", "matkhau123")) as cashier:
        renamed = await admin.patch(
            f"/api/admin/users/{member['id']}", json={"password": "matkhau-moi-456", "full_name": "Ngân 2"}
        )
        assert renamed.json()["full_name"] == "Ngân 2"
        assert (await cashier.get("/api/me")).status_code == 401
    assert (await login(db_app, "thu.ngan1", "matkhau123")).status_code == 401
    assert (await login(db_app, "thu.ngan1", "matkhau-moi-456")).status_code == 200

    me = (await admin.get("/api/me")).json()["user"]["id"]
    myself = await admin.patch(f"/api/admin/users/{me}", json={"is_active": False})
    assert myself.status_code == 409  # an admin cannot lock themselves out
    for bad in ({"is_active": "no"}, {"is_active": None}, {"password": "short"}):
        assert (await admin.patch(f"/api/admin/users/{member['id']}", json=bad)).status_code == 422, bad
    assert (await admin.patch("/api/admin/users/99999", json={"full_name": "x"})).status_code == 404


async def test_staff_cannot_manage_staff_or_read_reports(staff: httpx.AsyncClient) -> None:
    for response in (
        await staff.get("/api/admin/users"),
        await staff.post("/api/admin/users", json={"username": "abc", "password": "matkhau123"}),
        await staff.patch("/api/admin/users/1", json={"full_name": "x"}),
        await staff.get("/api/admin/reports?range=7d"),
    ):
        assert response.status_code == 403 and response.json()["code"] == "FORBIDDEN"


# ---------------------------------------------------------------- reports


async def test_report_of_an_empty_day(admin: httpx.AsyncClient) -> None:
    report = (await admin.get("/api/admin/reports")).json()
    assert report["range"] == "today" and len(report["daily"]) == 1
    assert (report["orders_today"], report["revenue_today"], report["captures_today"]) == (0, 0, 0)
    assert report["error_rate"] == 0.0 and report["avg_processing_ms"] is None
    assert (report["active_shifts"], report["active_staff"], report["queue_size"]) == (1, 2, 0)
    assert (report["products_total"], report["products_missing_price"], report["products_missing_barcode"]) == (3, 3, 2)


async def test_report_ranges_daily_revenue_and_best_sellers(
    db_app: FastAPI, admin: httpx.AsyncClient, staff: httpx.AsyncClient
) -> None:
    await _pay(db_app, staff, {"12": 3, "7": 1}, {"12": 10000, "7": 2000})  # 32 000
    await _pay(db_app, staff, {"12": 1}, {})  # 10 000
    await _pay(db_app, staff, {"8": 5}, {"8": 1000})  # 5 000
    report = (await admin.get("/api/admin/reports?range=7d")).json()
    assert report["range"] == "7d" and len(report["daily"]) == 7
    assert report["daily"][-1]["orders"] == 3 and report["daily"][-1]["revenue"] == 47000
    assert all(day["orders"] == 0 for day in report["daily"][:-1])  # days without a sale are there too
    assert report["daily"][0]["date"] < report["daily"][-1]["date"]
    assert (report["range_orders"], report["range_revenue"]) == (3, 47000)
    assert report["top_products"] == [
        {"product_id": "8", "name": "Sữa rửa mặt", "quantity": 5, "revenue": 5000},  # most units first
        {"product_id": "12", "name": "Son dưỡng", "quantity": 4, "revenue": 40000},
        {"product_id": "7", "name": "Kem dưỡng da", "quantity": 1, "revenue": 2000},
    ]
    assert (report["orders_today"], report["revenue_today"]) == (3, 47000)
    assert len((await admin.get("/api/admin/reports?range=30d")).json()["daily"]) == 30
    assert (await admin.get("/api/admin/reports?range=1y")).status_code == 422


async def test_a_voided_sale_leaves_the_reports(
    db_app: FastAPI, admin: httpx.AsyncClient, staff: httpx.AsyncClient
) -> None:
    paid = await _pay(db_app, staff, {"12": 2}, {"12": 10000})
    assert (await admin.get("/api/admin/reports?range=7d")).json()["range_revenue"] == 20000
    await admin.post(f"/api/orders/{paid['id']}/void")
    report = (await admin.get("/api/admin/reports?range=7d")).json()
    assert report["range_revenue"] == 0 and report["top_products"] == [] and report["orders_today"] == 0


async def test_report_counts_todays_photos_errors_and_average_time(db_app: FastAPI, admin: httpx.AsyncClient) -> None:
    from app.modules.captures.models import CaptureRow
    from app.modules.orders.models import OrderRow

    async with db_app.state.backends.engine.begin() as conn:
        order_id = (await conn.execute(insert(OrderRow).values(cashier_id=1).returning(OrderRow.id))).scalar_one()
        await conn.execute(
            insert(CaptureRow),
            [
                {"order_id": order_id, "job_status": "done", "processing_time_ms": 100.0},
                {"order_id": order_id, "job_status": "done", "processing_time_ms": 300.25},
                {"order_id": order_id, "job_status": "error", "processing_time_ms": 60000.0},
                {"order_id": order_id, "job_status": "error", "processing_time_ms": None},
            ],
        )
    report = (await admin.get("/api/admin/reports")).json()
    assert report["captures_today"] == 4 and report["error_rate"] == 0.5
    assert report["avg_processing_ms"] == 200.1  # of the successful ones only
