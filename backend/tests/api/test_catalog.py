"""Product search of the POS screen, on the engine's catalog tables plus the web's prices.
Ported from the legacy suite (src_legacy/tests/test_backend_api.py)."""

import json

import pytest
from fastapi import FastAPI
from sqlalchemy import insert
from sqlmodel import Session

from app.modules.catalog.models import ProductPriceRow
from app.modules.catalog.service import fold
from engine.catalog.db import ColorReference, Product, ProductEvidence, make_engine_from_url
from tests.api.conftest import STAFF_PASSWORD, http, token_for


@pytest.fixture
def catalog(catalog_url: str) -> None:
    """Five products: priced and not, with and without a barcode, one off sale, one unnamed."""
    engine = make_engine_from_url(catalog_url)
    with Session(engine) as session:
        session.add(Product(product_id="2", product_name="Son dưỡng BE203"))
        session.add(Product(product_id="3", product_name="Phấn má hồng Đào"))
        session.add(Product(product_id="10", product_name="Kem chống nắng", needs_naming=True))
        session.add(Product(product_id="12", product_name="Sữa rửa mặt", barcode="8931000009999"))
        session.add(Product(product_id="13", product_name="Phấn phủ (ngừng bán)", is_active=False))
        session.add(ColorReference(color_code="BE203", r=109, g=63, b=62, hex="#6D3F3E"))
        session.commit()
        session.add(ProductEvidence(product_id="2", evidence_type="color_code", value_json=json.dumps("BE203")))
        session.add(ProductEvidence(product_id="3", evidence_type="color_code", value_json=json.dumps("PK999")))
        session.add(ProductEvidence(product_id="3", evidence_type="ocr_keywords", value_json=json.dumps(["ma hong"])))
        session.commit()
    engine.dispose()


async def _search(app: FastAPI, query: str = "") -> list[dict[str, object]]:
    token = await token_for(app, "staff", STAFF_PASSWORD)
    async with http(app, token) as client:
        response = await client.get(f"/api/catalog/products{query}")
    assert response.status_code == 200, response.text
    items: list[dict[str, object]] = response.json()["items"]
    return items


def test_fold_drops_vietnamese_accents() -> None:
    assert fold("Phấn Má Hồng ĐÀO") == "phan ma hong dao"


async def test_catalog_needs_a_login(db_app: FastAPI, catalog: None) -> None:
    async with http(db_app) as anonymous:
        response = await anonymous.get("/api/catalog/products")
    assert response.status_code == 401 and response.json()["code"] == "AUTH_INVALID"


async def test_listing_shows_products_on_sale_in_catalog_order_with_prices(db_app: FastAPI, catalog: None) -> None:
    async with db_app.state.backends.engine.begin() as conn:
        await conn.execute(insert(ProductPriceRow).values(product_id="12", price=45000))
    items = await _search(db_app)
    assert [p["id"] for p in items] == ["2", "3", "10", "12"]  # "10" after "3"; "13" is off sale
    assert items[3] == {
        "id": "12",
        "name": "Sữa rửa mặt",
        "barcode": "8931000009999",
        "price": 45000,
        "needs_naming": False,
        "missing_color_reference": False,
    }
    assert items[0]["price"] is None and items[0]["barcode"] == ""
    assert items[2]["needs_naming"] is True
    # "2" names a colour that has a reference, "3" one that has none
    assert [p["missing_color_reference"] for p in items] == [False, True, False, False]


async def test_search_by_barcode_name_without_accents_and_id(db_app: FastAPI, catalog: None) -> None:
    assert [p["id"] for p in await _search(db_app, "?barcode=8931000009999")] == ["12"]
    assert await _search(db_app, "?barcode=0000") == []
    assert [p["id"] for p in await _search(db_app, "?search=phan%20ma%20hong")] == ["3"]
    assert [p["id"] for p in await _search(db_app, "?search=%20KEM%20")] == ["10"]
    assert [p["id"] for p in await _search(db_app, "?search=12")] == ["12"]  # the whole id, not a part of it
    assert await _search(db_app, "?search=phan%20phu") == []  # off sale
    # a barcode wins over the search text
    assert [p["id"] for p in await _search(db_app, "?search=kem&barcode=8931000009999")] == ["12"]


async def test_an_empty_catalog_is_an_empty_list(db_app: FastAPI, catalog_url: str) -> None:
    assert await _search(db_app) == []
