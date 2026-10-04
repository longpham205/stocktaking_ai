"""Admin edits of 3e-1: shop settings, prices, the change log and its reverts, the advanced password.
Ported from the legacy suite (src_legacy/tests/test_backend_api.py)."""

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import insert, select
from sqlmodel import Session

from app.modules.audit.models import ChangeLogRow
from app.modules.auth.ports import CurrentUser
from engine.catalog.db import Product, make_engine_from_url
from entrypoints.reset_password import main as reset_password_main
from entrypoints.reset_password import reset_advanced_password
from tests.api.conftest import ADMIN_PASSWORD, STAFF_PASSWORD, http, token_for


@pytest.fixture
def catalog(catalog_url: str) -> None:
    engine = make_engine_from_url(catalog_url)
    with Session(engine) as session:
        session.add(Product(product_id="2", product_name="Bút chì chân mày", barcode="8931000001372"))
        session.add(Product(product_id="3", product_name="Phấn má hồng"))
        session.add(Product(product_id="10", product_name="Kem chống nắng", needs_naming=True))
        session.add(Product(product_id="13", product_name="Phấn phủ", is_active=False))
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


async def _log(client: httpx.AsyncClient, query: str = "") -> list[dict[str, Any]]:
    response = await client.get(f"/api/admin/change-log{query}")
    assert response.status_code == 200, response.text
    items: list[dict[str, Any]] = response.json()["items"]
    return items


async def test_staff_cannot_use_the_admin_routes(staff: httpx.AsyncClient) -> None:
    for response in (
        await staff.patch("/api/admin/settings", json={"auto_print_receipt": True}),
        await staff.get("/api/admin/products"),
        await staff.patch("/api/admin/products/2", json={"price": 1}),
        await staff.get("/api/admin/change-log"),
        await staff.post("/api/admin/change-log/1/revert"),
    ):
        assert response.status_code == 403 and response.json()["code"] == "FORBIDDEN"


async def test_settings_change_validation_log_and_revert(admin: httpx.AsyncClient, staff: httpx.AsyncClient) -> None:
    response = await admin.patch("/api/admin/settings", json={"tilt_block_capture": True, "auto_print_receipt": True})
    assert response.status_code == 200
    assert response.json()["tilt_block_capture"] is True and response.json()["auto_print_receipt"] is True
    got = (await staff.get("/api/settings")).json()  # everyone sees it at once
    assert got["tilt_block_capture"] is True and got["auto_print_receipt"] is True
    for bad in (
        {"tilt_block_capture": "yes"},
        {"auto_print_receipt": 1},
        {"allow_checkout_without_price": None},
        {"similarity_threshold": 0.2},
        {"similarity_threshold": True},
        {"min_confidence_accept": 1.0},
        {"something_else": 1},  # nothing known to change
        {},
    ):
        response = await admin.patch("/api/admin/settings", json=bad)
        assert response.status_code == 422 and response.json()["code"] == "VALIDATION_ERROR", bad

    response = await admin.patch("/api/admin/settings", json={"similarity_threshold": 0.8})
    assert response.json()["similarity_threshold"] == 0.8
    entries = await _log(admin, "?table=settings")
    assert [(e["record_id"], e["field"], e["old"], e["new"]) for e in entries] == [
        ("similarity_threshold", "similarity_threshold", "null", "0.8"),
        ("auto_print_receipt", "auto_print_receipt", "false", "true"),
        ("tilt_block_capture", "tilt_block_capture", "false", "true"),
    ]
    assert entries[0]["by"] == "admin" and entries[0]["name"] is None
    revert = await admin.post(f"/api/admin/change-log/{entries[0]['id']}/revert")
    assert revert.status_code == 200
    assert revert.json() == {
        "ok": True,
        "reverted": {"table": "settings", "record_id": "similarity_threshold", "field": "similarity_threshold"},
    }
    assert (await staff.get("/api/settings")).json()["similarity_threshold"] is None
    stale = await admin.post(f"/api/admin/change-log/{entries[0]['id']}/revert")  # no longer 0.8
    assert stale.status_code == 409 and stale.json()["code"] == "CHANGE_STALE"
    assert len(await _log(admin, "?table=settings")) == 4  # the revert is logged too


async def test_admin_product_list_search_filters_and_pages(db_app: FastAPI, admin: httpx.AsyncClient) -> None:
    await admin.patch("/api/admin/products/3", json={"price": 15000})
    body = (await admin.get("/api/admin/products")).json()
    assert body["total"] == 3 and [p["id"] for p in body["items"]] == ["2", "3", "10"]  # "13" is off sale
    assert (body["missing_price"], body["missing_barcode"], body["needs_naming"]) == (2, 2, 1)
    assert [p["id"] for p in (await admin.get("/api/admin/products?search=chan%20may")).json()["items"]] == ["2"]
    assert [p["id"] for p in (await admin.get("/api/admin/products?search=0001372")).json()["items"]] == ["2"]
    for name, ids in (("missing_price", ["2", "10"]), ("missing_barcode", ["3", "10"]), ("needs_naming", ["10"])):
        assert [p["id"] for p in (await admin.get(f"/api/admin/products?filter={name}")).json()["items"]] == ids
    page = (await admin.get("/api/admin/products?page=2&size=2")).json()
    assert (page["total"], page["page"], page["size"], [p["id"] for p in page["items"]]) == (3, 2, 2, ["10"])
    assert (await admin.get("/api/admin/products?filter=lung-tung")).status_code == 422


async def test_price_update_validation_and_log(admin: httpx.AsyncClient, staff: httpx.AsyncClient) -> None:
    response = await admin.patch("/api/admin/products/2", json={"price": 15000})
    assert response.status_code == 200
    assert response.json() == {
        "id": "2",
        "name": "Bút chì chân mày",
        "barcode": "8931000001372",
        "price": 15000,
        "needs_naming": False,
        "missing_color_reference": False,
    }
    assert (await staff.get("/api/catalog/products?search=chan%20may")).json()["items"][0]["price"] == 15000
    await admin.patch("/api/admin/products/2", json={"price": 15000})  # unchanged: not logged
    for bad in ({"price": -5}, {"price": 1.5}, {"price": "100"}, {"foo": "x"}, {}):
        assert (await admin.patch("/api/admin/products/2", json=bad)).status_code == 422, bad
    missing = await admin.patch("/api/admin/products/404", json={"price": 1})
    assert missing.status_code == 404 and missing.json()["code"] == "NOT_FOUND"
    assert (
        await admin.patch("/api/admin/products/13", json={"price": 9000})
    ).status_code == 200  # off sale: still priced
    assert (await admin.patch("/api/admin/products/2", json={"price": None})).json()["price"] is None
    entries = await _log(admin, "?table=product&record=2")
    assert [(e["field"], e["old"], e["new"]) for e in entries] == [("price", "15000", None), ("price", None, "15000")]
    assert entries[0]["name"] == "Bút chì chân mày"


async def test_price_revert_chain_and_what_cannot_be_reverted(db_app: FastAPI, admin: httpx.AsyncClient) -> None:
    await admin.patch("/api/admin/products/2", json={"price": 1000})
    await admin.patch("/api/admin/products/2", json={"price": 2000})
    newest, oldest = await _log(admin, "?table=product&record=2")

    async def price() -> Any:
        return (await admin.get("/api/admin/products?search=chan%20may")).json()["items"][0]["price"]

    assert (await admin.post(f"/api/admin/change-log/{newest['id']}/revert")).status_code == 200  # 2000 -> 1000
    assert await price() == 1000 and len(await _log(admin, "?table=product&record=2")) == 3
    assert (await admin.post(f"/api/admin/change-log/{oldest['id']}/revert")).status_code == 200  # 1000 -> none
    assert await price() is None
    stale = await admin.post(f"/api/admin/change-log/{newest['id']}/revert")  # the price is no longer 2000
    assert stale.status_code == 409 and stale.json()["code"] == "CHANGE_STALE"
    assert (await admin.post("/api/admin/change-log/99999/revert")).status_code == 404
    # not revertible: a product field nobody edits, a table nobody registered (engine settings: 3e-4)
    async with db_app.state.backends.engine.begin() as conn:
        rows = [
            {"table_name": "product", "record_id": "2", "field_name": "brand", "old_value": "", "new_value": "1"},
            {
                "table_name": "config",
                "record_id": "retrieval.top_k",
                "field_name": "retrieval.top_k",
                "new_value": "6",
                "old_value": None,
            },
        ]
        ids = (await conn.execute(insert(ChangeLogRow).returning(ChangeLogRow.id), rows)).scalars().all()
    for entry_id in ids:
        response = await admin.post(f"/api/admin/change-log/{entry_id}/revert")
        assert response.status_code == 422 and response.json()["code"] == "VALIDATION_ERROR"
    assert len(await _log(admin, "?limit=2")) == 2


async def test_advanced_password_not_set_wrong_locked_and_right(db_app: FastAPI) -> None:
    service = db_app.state.backends.pos_settings
    admin = CurrentUser(user_id=2, role="admin", shift_id=1, username="admin", full_name="")
    with pytest.raises(Exception, match="Chưa có mật khẩu nâng cao") as not_set:
        await service.verify_advanced_password(admin, "anything")
    assert not_set.value.code == "ADVANCED_PASSWORD_NOT_SET" and not_set.value.status_code == 409

    with pytest.raises(ValueError):
        await reset_advanced_password(service.repo, "short")
    await reset_advanced_password(service.repo, "advanced-pass-789")
    await service.verify_advanced_password(admin, "advanced-pass-789")
    async with db_app.state.backends.engine.connect() as conn:  # the hash never reaches the change log
        assert (await conn.execute(select(ChangeLogRow.id))).all() == []
    assert "advanced_password_hash" not in (await service.public()).model_dump()

    codes = []
    for attempt in ("sai-1", None, "sai-3", "advanced-pass-789"):  # the test limit is 3 wrong tries
        try:
            await service.verify_advanced_password(admin, attempt)
            codes.append("ok")
        except Exception as exc:
            codes.append(getattr(exc, "code", repr(exc)))
    assert codes == ["ADVANCED_PASSWORD_INVALID"] * 3 + ["RATE_LIMITED"]


def test_reset_password_entrypoint_needs_an_account_or_advanced() -> None:
    for argv in ([], ["admin", "--advanced"]):
        with pytest.raises(SystemExit) as exit_:
            reset_password_main(argv)
        assert exit_.value.code == 2
