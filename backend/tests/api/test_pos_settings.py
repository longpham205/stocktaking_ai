"""The settings a logged-in account reads: defaults, then whatever the `settings` table holds."""

from typing import Any

from fastapi import FastAPI
from sqlalchemy import insert

from app.modules.pos_settings.models import SettingRow
from tests.api.conftest import STAFF_PASSWORD, http, token_for

DEFAULTS = {
    "allow_checkout_without_price": False,
    "similarity_threshold": None,
    "min_confidence_accept": None,
    "tilt_block_capture": False,
    "auto_print_receipt": False,
}


async def _store(app: FastAPI, **values: Any) -> None:
    async with app.state.backends.engine.begin() as conn:
        await conn.execute(insert(SettingRow), [{"key": key, "value": value} for key, value in values.items()])


async def test_settings_need_a_login(db_app: FastAPI) -> None:
    async with http(db_app) as anonymous:
        response = await anonymous.get("/api/settings")
    assert response.status_code == 401 and response.json()["code"] == "AUTH_INVALID"


async def test_settings_start_from_the_defaults_and_me_carries_them(db_app: FastAPI) -> None:
    token = await token_for(db_app, "staff", STAFF_PASSWORD)
    async with http(db_app, token) as client:
        response = await client.get("/api/settings")
        me = (await client.get("/api/me")).json()
    assert response.status_code == 200 and response.json() == DEFAULTS
    assert me["settings"] == DEFAULTS


async def test_stored_values_replace_the_defaults(db_app: FastAPI) -> None:
    await _store(
        db_app,
        allow_checkout_without_price=True,
        similarity_threshold=0.62,
        auto_print_receipt=True,
        advanced_password_hash="scrypt$not-public",
    )
    token = await token_for(db_app, "staff", STAFF_PASSWORD)
    async with http(db_app, token) as client:
        got = (await client.get("/api/settings")).json()
        me = (await client.get("/api/me")).json()
    assert got == {
        **DEFAULTS,
        "allow_checkout_without_price": True,
        "similarity_threshold": 0.62,
        "auto_print_receipt": True,
    }
    assert me["settings"] == got  # and nothing else from the table, such as the advanced-password hash
