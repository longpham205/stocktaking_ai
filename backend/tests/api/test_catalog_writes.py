"""Admin writes to the engine's catalog (3e-2): barcode and name, recognition evidence, colour
references, gallery photos, their reverts, and the recognizer re-reading the catalog afterwards.
Ported from the legacy suite (src_legacy/tests/test_backend_api.py)."""

import io
import json
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from PIL import Image
from sqlmodel import Session

from app.modules.recognition.ports import Recognition
from engine.catalog.db import Product, ProductEvidence, make_engine_from_url
from engine.catalog.repository import DatabaseCatalogRepository
from tests.api.conftest import ADMIN_PASSWORD, http, seed_accounts, start_app, token_for


class CountingRecognizer:
    """Counts how often the catalog is re-read."""

    name = "counting"

    def __init__(self) -> None:
        self.reloads = 0

    def recognize(
        self, image_path: str, similarity_threshold: float | None = None, min_confidence_accept: float | None = None
    ) -> Recognition:
        return Recognition([], 1.0)

    def reload_catalog(self) -> None:
        self.reloads += 1

    def close(self) -> None:
        return None


def _evidence(pid: str, evidence_type: str, value: Any) -> ProductEvidence:
    return ProductEvidence(product_id=pid, evidence_type=evidence_type, value_json=json.dumps(value))


@pytest.fixture
def catalog(catalog_url: str) -> None:
    engine = make_engine_from_url(catalog_url)
    with Session(engine) as session:
        session.add(Product(product_id="2", product_name="Bút chì chân mày"))
        session.add(Product(product_id="3", product_name="Phấn Má Hồng"))
        session.add(Product(product_id="7", product_name="Kem ABA", gallery_folder="kem_aba"))
        session.add(Product(product_id="8", product_name="Kem ABC"))
        session.add(Product(product_id="12", product_name="Son dưỡng", barcode="8931000009999"))
        session.commit()
        session.add(_evidence("2", "color_code", "BR641"))  # a colour with no reference yet
        for pid, other, keyword in (("7", "8", "ABA"), ("8", "7", "ABC")):  # a pair that forces OCR
            session.add(_evidence(pid, "confusable_with", [other]))
            session.add(_evidence(pid, "force_evidence", ["ocr"]))
            session.add(_evidence(pid, "ocr_keywords", [keyword]))
        session.commit()
    engine.dispose()


@pytest.fixture
def pipeline_config(tmp_path: Path) -> Path:
    gallery = tmp_path / "gallery" / "kem_aba"
    gallery.mkdir(parents=True)
    for name in ("1.jpg", "2.jpg"):
        Image.new("RGB", (3000, 2000), (200, 120, 40)).save(gallery / name, "JPEG")
    config = tmp_path / "pipeline.yaml"
    config.write_text(
        f"paths:\n  gallery_dir: {json.dumps((tmp_path / 'gallery').as_posix())}\n"
        "plugins:\n  ocr:\n    min_text_length: 3\n",
        encoding="utf-8",
    )
    return config


@pytest.fixture
async def app(database_url: str, catalog: None, pipeline_config: Path) -> AsyncIterator[FastAPI]:
    started = await start_app(database_url, recognizer=CountingRecognizer(), core={"pipeline_config": pipeline_config})
    async with AsyncExitStack() as stack:
        await stack.enter_async_context(started.router.lifespan_context(started))
        await seed_accounts(started)
        yield started


@pytest.fixture
async def admin(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with http(app, await token_for(app, "admin", ADMIN_PASSWORD)) as client:
        yield client


def _reloads(app: FastAPI) -> int:
    return int(app.state.backends.recognizer.reloads)


def _engine_view(catalog_url: str) -> DatabaseCatalogRepository:
    """The catalog as the engine reads it."""
    return DatabaseCatalogRepository(catalog_url)


async def _log(admin: httpx.AsyncClient, query: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = (await admin.get(f"/api/admin/change-log{query}")).json()["items"]
    return items


async def test_barcode_and_name_live_in_the_engine_catalog_and_reload_the_recognizer(
    app: FastAPI, admin: httpx.AsyncClient, catalog_url: str
) -> None:
    response = await admin.patch("/api/admin/products/3", json={"barcode": " 89310000333 "})
    assert response.status_code == 200 and response.json()["barcode"] == "89310000333"
    assert _engine_view(catalog_url).get_product("3")["barcode"] == "89310000333"
    assert _reloads(app) == 1
    assert (await admin.patch("/api/admin/products/3", json={"price": 5000})).status_code == 200
    assert _reloads(app) == 1  # the price is the web's: the recognizer is not reloaded

    duplicate = await admin.patch("/api/admin/products/3", json={"barcode": "8931000009999"})
    assert duplicate.status_code == 409 and duplicate.json()["code"] == "BARCODE_DUPLICATE"
    for bad in ({"barcode": "a b!"}, {"name": "  "}, {"name": "x" * 121}):
        assert (await admin.patch("/api/admin/products/3", json=bad)).status_code == 422, bad
    # one request, one transaction: a refused barcode leaves the price alone too
    assert (
        await admin.patch("/api/admin/products/3", json={"price": 9, "barcode": "8931000009999"})
    ).status_code == 409
    assert (await admin.get("/api/admin/products?search=ma%20hong")).json()["items"][0]["price"] == 5000

    renamed = (await admin.patch("/api/admin/products/3", json={"name": "  Phấn má   hồng mới "})).json()
    assert renamed["name"] == "Phấn má hồng mới" and renamed["needs_naming"] is False
    entries = await _log(admin, "?table=product&record=3")
    assert [(e["field"], e["old"], e["new"]) for e in entries] == [
        ("name", "Phấn Má Hồng", "Phấn má hồng mới"),
        ("price", None, "5000"),
        ("barcode", "", "89310000333"),
    ]
    assert (await admin.post(f"/api/admin/change-log/{entries[0]['id']}/revert")).status_code == 200
    assert _engine_view(catalog_url).get_product("3")["product_name"] == "Phấn Má Hồng"
    assert (await admin.post(f"/api/admin/change-log/{entries[2]['id']}/revert")).status_code == 200
    assert _engine_view(catalog_url).get_product("3")["barcode"] is None
    assert _reloads(app) == 4


async def test_a_barcode_revert_is_refused_once_another_product_took_the_code(admin: httpx.AsyncClient) -> None:
    await admin.patch("/api/admin/products/2", json={"barcode": "89310000555"})
    await admin.patch("/api/admin/products/2", json={"barcode": ""})
    cleared = (await _log(admin, "?table=product&record=2"))[0]  # "89310000555" -> ""
    await admin.patch("/api/admin/products/3", json={"barcode": "89310000555"})
    response = await admin.post(f"/api/admin/change-log/{cleared['id']}/revert")
    assert response.status_code == 409 and response.json()["code"] == "BARCODE_DUPLICATE"


async def test_evidence_needs_confirmation_is_normalised_validated_and_symmetric(
    app: FastAPI, admin: httpx.AsyncClient, catalog_url: str
) -> None:
    view = (await admin.get("/api/admin/products/7/evidence")).json()
    assert view["evidence"] == {
        "ocr_keywords": ["ABA"],
        "color_code": None,
        "force_evidence": ["ocr"],
        "confusable_with": ["8"],
    }
    assert view["confirm_text"] and view["ocr_min_length"] == 3 and view["product"]["id"] == "7"
    unconfirmed = await admin.patch("/api/admin/products/3/evidence", json={"ocr_keywords": ["OR210"]})
    assert unconfirmed.status_code == 422 and unconfirmed.json()["code"] == "CONFIRM_REQUIRED"
    assert unconfirmed.json()["confirm_text"] == view["confirm_text"]

    ok = await admin.patch("/api/admin/products/3/evidence", json={"ocr_keywords": ["or210", "OR210"], "confirm": True})
    assert ok.status_code == 200 and ok.json()["evidence"]["ocr_keywords"] == ["OR210"]  # upper case, no duplicates
    assert _engine_view(catalog_url).ocr_keywords("3") == ("OR210",) and _reloads(app) == 1

    invalid = await admin.patch("/api/admin/products/8/evidence", json={"ocr_keywords": ["ABA"], "confirm": True})
    assert invalid.status_code == 422 and invalid.json()["code"] == "EVIDENCE_INVALID" and invalid.json()["errors"]
    for bad in (
        {"ocr_keywords": ["AB"]},
        {"force_evidence": ["sam2"]},
        {"confusable_with": ["3"]},
        {"confusable_with": ["99"]},
        {"color_code": "x"},
        {"ocr_keywords": "OR210"},
        {"something": 1},
    ):
        response = await admin.patch("/api/admin/products/3/evidence", json={**bad, "confirm": True})
        assert response.status_code == 422, bad
    assert _reloads(app) == 1  # nothing refused was saved

    # a new pair 3 <-> 12 is written on both; removing 7 <-> 8 removes both sides
    await admin.patch("/api/admin/products/3/evidence", json={"confusable_with": ["12"], "confirm": True})
    assert _engine_view(catalog_url).evidence("12", "confusable_with") == ("3",)
    await admin.patch(
        "/api/admin/products/7/evidence", json={"confusable_with": [], "force_evidence": [], "confirm": True}
    )
    engine_view = _engine_view(catalog_url)
    assert frozenset({"7", "8"}) not in engine_view.confusable_pairs()
    assert engine_view.evidence("8", "confusable_with") is None

    # reverting the removal on 7 brings the pair back on both sides
    entry = next(e for e in await _log(admin, "?table=product_evidence&record=7") if e["field"] == "confusable_with")
    assert entry["name"] == "Kem ABA"
    assert (await admin.post(f"/api/admin/change-log/{entry['id']}/revert")).status_code == 200
    assert frozenset({"7", "8"}) in _engine_view(catalog_url).confusable_pairs()
    stale = await admin.post(f"/api/admin/change-log/{entry['id']}/revert")
    assert stale.status_code == 409 and stale.json()["code"] == "CHANGE_STALE"
    async with http(app) as anonymous:
        assert (await anonymous.get("/api/admin/products/7/evidence")).status_code == 401
    assert (await admin.get("/api/admin/products/404/evidence")).status_code == 404


async def test_colour_references_listing_flags_edits_and_revert(
    app: FastAPI, admin: httpx.AsyncClient, catalog_url: str
) -> None:
    items = (await admin.get("/api/admin/colors")).json()["items"]
    assert next(c for c in items if c["code"] == "BR641") == {
        "code": "BR641",
        "hex": None,
        "r": None,
        "g": None,
        "b": None,
        "source": None,
        "used_by": ["2"],
        "missing": True,
    }
    assert (await admin.get("/api/admin/products?search=chan%20may")).json()["items"][0]["missing_color_reference"]
    unconfirmed = await admin.patch("/api/admin/colors/BR641", json={"hex": "#5A3C2D"})
    assert unconfirmed.status_code == 422 and unconfirmed.json()["code"] == "CONFIRM_REQUIRED"
    for bad_code, body in (("BR641", {"hex": "zz"}), ("BR641", {}), ("x", {"hex": "#000000"})):
        assert (await admin.patch(f"/api/admin/colors/{bad_code}", json={**body, "confirm": True})).status_code == 422

    colour = (await admin.patch("/api/admin/colors/br641", json={"hex": "5a3c2d", "confirm": True})).json()
    assert colour == {
        "code": "BR641",
        "hex": "#5A3C2D",
        "r": 90,
        "g": 60,
        "b": 45,
        "source": "manual",
        "used_by": ["2"],
        "missing": False,
    }
    assert _engine_view(catalog_url).color_references()["BR641"] == (90, 60, 45) and _reloads(app) == 1
    assert not (await admin.get("/api/admin/products?search=chan%20may")).json()["items"][0]["missing_color_reference"]
    entry = (await _log(admin, "?table=color_reference"))[0]
    assert (entry["record_id"], entry["field"], entry["old"], entry["new"]) == ("BR641", "hex", None, "#5A3C2D")
    assert (await admin.post(f"/api/admin/change-log/{entry['id']}/revert")).status_code == 200
    assert "BR641" not in _engine_view(catalog_url).color_references()


async def test_gallery_photos_are_signed_and_scaled_down(app: FastAPI, admin: httpx.AsyncClient) -> None:
    urls = (await admin.get("/api/admin/products/7/evidence")).json()["gallery"]
    assert len(urls) == 2 and (await admin.get("/api/admin/products/8/evidence")).json()["gallery"] == []
    async with http(app) as anonymous:  # signed: no token
        response = await anonymous.get(urls[1])
        assert response.status_code == 200 and response.headers["content-type"] == "image/jpeg"
        assert max(Image.open(io.BytesIO(response.content)).size) == 1024
        forged = await anonymous.get(urls[0].replace("sig=", "sig=x"))
        assert forged.status_code == 403 and forged.json()["code"] == "FORBIDDEN"
        beyond = urls[0].replace("/7/0?", "/7/5?")
        assert (await anonymous.get(beyond)).status_code == 403  # the signature covers the index
