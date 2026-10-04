"""Advanced engine settings, the benchmark validation and the evidence test (3e-4), with a stub
recognizer. Ported from the legacy suite (tests/test_backend_api.py of web v1, git commit f30710d)."""

import asyncio
import io
import json
import threading
import time
from collections.abc import AsyncIterator, Callable
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from PIL import Image
from sqlmodel import Session

from app.modules.recognition.ports import Detection, Recognition
from engine.catalog.db import ColorReference, Product, ProductEvidence, make_engine_from_url
from entrypoints.reset_password import reset_advanced_password
from tests.api.conftest import ADMIN_PASSWORD, STAFF_PASSWORD, http, seed_accounts, start_app, token_for

BACKEND_DIR = Path(__file__).resolve().parents[2]
DEMO_CONFIG = BACKEND_DIR / "configs" / "config.demo.yaml"
ADVANCED = "advanced-pass-789"
OK = {"advanced_password": ADVANCED, "confirm": True}


class StubRecognizer:
    """Records what it is asked; `gate` holds a reload or a validation until the test lets it go."""

    name = "stub"

    def __init__(self) -> None:
        self.script: Callable[[str], Recognition] = lambda _path: Recognition([], 1.0)
        self.overrides: dict[str, Any] = {}
        self.fail_reload = False
        self.gate = threading.Event()
        self.gate.set()

    def recognize(
        self, image_path: str, similarity_threshold: float | None = None, min_confidence_accept: float | None = None
    ) -> Recognition:
        return self.script(image_path)

    def reload_catalog(self) -> None:
        return None

    def reload_pipeline(self, overrides: dict[str, Any]) -> None:
        self.gate.wait(10)
        if self.fail_reload:
            raise RuntimeError("giả lập: không nạp được pipeline")
        self.overrides = dict(overrides)

    def validate(self, benchmark_dir: str | None, output_dir: Path) -> dict[str, Any]:
        self.gate.wait(10)
        return {"end_to_end": {"f1": 0.5}, "fusion": {"accuracy_after": 0.6}}

    def close(self) -> None:
        return None


def _jpeg() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (320, 240), (90, 90, 90)).save(out, "JPEG")
    return out.getvalue()


@pytest.fixture
def catalog(catalog_url: str) -> None:
    engine = make_engine_from_url(catalog_url)
    with Session(engine) as session:
        session.add(Product(product_id="1", product_name="Sữa rửa mặt", barcode="8931000001372"))
        session.add(Product(product_id="7", product_name="Kem ABA"))
        session.add(ColorReference(color_code="BE203", r=200, g=160, b=150, hex="#C8A096"))
        session.commit()
        session.add(ProductEvidence(product_id="7", evidence_type="ocr_keywords", value_json=json.dumps(["ABA"])))
        session.commit()
    engine.dispose()


@pytest.fixture
def stub() -> StubRecognizer:
    return StubRecognizer()


@pytest.fixture
async def app(database_url: str, catalog: None, stub: StubRecognizer, tmp_path: Path) -> AsyncIterator[FastAPI]:
    started = await start_app(
        database_url,
        recognizer=stub,
        core={"pipeline_config": DEMO_CONFIG, "media_dir": tmp_path / "media", "data_dir": tmp_path},
    )
    async with AsyncExitStack() as stack:
        await stack.enter_async_context(started.router.lifespan_context(started))
        await seed_accounts(started)
        yield started


@pytest.fixture
async def admin(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    await reset_advanced_password(app.state.backends.pos_settings.repo, ADVANCED)
    async with http(app, await token_for(app, "admin", ADMIN_PASSWORD)) as client:
        yield client


@pytest.fixture
async def staff(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with http(app, await token_for(app, "staff", STAFF_PASSWORD)) as client:
        yield client


async def _overrides(app: FastAPI) -> dict[str, Any]:
    stored: dict[str, Any] = await app.state.backends.engine_config.repo.overrides()
    return stored


# ---------------------------------------------------------------- advanced settings


async def test_config_view_apply_failed_reload_reset_and_revert(
    app: FastAPI, admin: httpx.AsyncClient, staff: httpx.AsyncClient, stub: StubRecognizer
) -> None:
    config = (await admin.get("/api/admin/config")).json()
    assert config["advanced_password_set"] and config["config_error"] is None and not config["reloading"]
    assert config["pipeline_config"] == "config.demo.yaml"
    by_key = {item["key"]: item for item in config["items"]}
    assert by_key["retrieval.backend"]["tier"] == "readonly"
    assert by_key["retrieval.backend"]["value"] == "mock_visual_embedding"
    threshold = by_key["detection.confidence_threshold"]
    assert threshold["tier"] == "reload" and threshold["value"] == threshold["default"] and not threshold["overridden"]

    change = {"detection.confidence_threshold": 0.6}
    unconfirmed = await admin.post("/api/admin/config/apply", json={"changes": change, "advanced_password": ADVANCED})
    assert unconfirmed.status_code == 422 and unconfirmed.json()["code"] == "CONFIRM_REQUIRED"
    wrong = await admin.post(
        "/api/admin/config/apply", json={"changes": change, "advanced_password": STAFF_PASSWORD, "confirm": True}
    )
    assert wrong.status_code == 403 and wrong.json()["code"] == "ADVANCED_PASSWORD_INVALID"
    for bad in (
        {"retrieval.backend": "x"},  # shown only
        {"detection.confidence_threshold": 2},
        {"plugins.ocr.enabled": "yes"},
        {"khong.co": 1},
        {},
    ):
        response = await admin.post("/api/admin/config/apply", json={"changes": bad, **OK})
        assert response.status_code == 422 and response.json()["code"] == "VALIDATION_ERROR", bad

    applied = await admin.post(
        "/api/admin/config/apply", json={"changes": {**change, "plugins.ocr.enabled": True}, **OK}
    )
    assert applied.status_code == 200 and applied.json()["applied"] is True
    assert stub.overrides == {"detection.confidence_threshold": 0.6, "plugins.ocr.enabled": True}
    by_key = {item["key"]: item for item in applied.json()["items"]}
    assert (
        by_key["detection.confidence_threshold"]["value"] == 0.6
        and by_key["detection.confidence_threshold"]["overridden"]
    )
    again = await admin.post("/api/admin/config/apply", json={"changes": change, **OK})
    assert again.json()["applied"] is False  # nothing new: no reload

    # a reload that fails: reported, and neither the database nor the recognizer changes
    stub.fail_reload = True
    failed = await admin.post("/api/admin/config/apply", json={"changes": {"retrieval.top_k": 7}, **OK})
    assert failed.status_code == 500 and failed.json()["code"] == "RELOAD_FAILED"
    stub.fail_reload = False
    assert "retrieval.top_k" not in await _overrides(app)

    # null: back to the YAML's value, the override is gone
    reset = await admin.post("/api/admin/config/apply", json={"changes": {"plugins.ocr.enabled": None}, **OK})
    assert reset.status_code == 200 and await _overrides(app) == {"detection.confidence_threshold": 0.6}

    # a revert needs the advanced password too
    entries = (await admin.get("/api/admin/change-log?table=config")).json()["items"]
    entry = next(e for e in entries if e["field"] == "detection.confidence_threshold")
    assert (entry["old"], entry["new"]) == (None, "0.6")
    assert (await admin.post(f"/api/admin/change-log/{entry['id']}/revert")).status_code == 403
    reverted = await admin.post(f"/api/admin/change-log/{entry['id']}/revert", json={"advanced_password": ADVANCED})
    assert reverted.status_code == 200
    assert await _overrides(app) == {} and stub.overrides == {}

    assert (await staff.get("/api/admin/config")).status_code == 403
    assert (await staff.post("/api/admin/config/apply", json={"changes": change, **OK})).status_code == 403


async def test_apply_without_an_advanced_password_set(app: FastAPI) -> None:
    async with http(app, await token_for(app, "admin", ADMIN_PASSWORD)) as admin:
        assert (await admin.get("/api/admin/config")).json()["advanced_password_set"] is False
        response = await admin.post(
            "/api/admin/config/apply",
            json={"changes": {"retrieval.top_k": 6}, "advanced_password": "x", "confirm": True},
        )
    assert response.status_code == 409 and response.json()["code"] == "ADVANCED_PASSWORD_NOT_SET"


async def test_jobs_report_a_reload_and_a_second_reload_waits_its_turn(
    app: FastAPI, admin: httpx.AsyncClient, staff: httpx.AsyncClient, stub: StubRecognizer
) -> None:
    stub.gate.clear()
    first = asyncio.create_task(admin.post("/api/admin/config/apply", json={"changes": {"retrieval.top_k": 6}, **OK}))
    try:
        deadline = time.monotonic() + 10
        while not app.state.backends.worker.reloading and time.monotonic() < deadline:
            await asyncio.sleep(0.02)
        order_id = (await staff.post("/api/orders")).json()["id"]
        job_id = (await staff.post(f"/api/orders/{order_id}/captures", content=_jpeg())).json()["job_id"]
        job = (await staff.get(f"/api/jobs/{job_id}")).json()
        assert job["status"] in ("queued", "processing") and job["system_reloading"] is True
        second = await admin.post("/api/admin/config/apply", json={"changes": {"retrieval.top_k": 7}, **OK})
        assert second.status_code == 409 and second.json()["code"] == "RELOAD_IN_PROGRESS"
        assert (await admin.get("/api/admin/config")).json()["reloading"] is True
    finally:
        stub.gate.set()
    assert (await first).json()["applied"] is True and stub.overrides == {"retrieval.top_k": 6}


# ---------------------------------------------------------------- validation


async def test_validation_needs_confirmation_blocks_captures_then_reports(
    app: FastAPI, admin: httpx.AsyncClient, staff: httpx.AsyncClient, stub: StubRecognizer
) -> None:
    idle = (await admin.get("/api/admin/validation")).json()
    assert idle["status"] == "idle" and set(idle["baseline"]) == {"f1", "fusion_accuracy"}
    unconfirmed = await admin.post("/api/admin/validation", json={"advanced_password": ADVANCED})
    assert unconfirmed.status_code == 422 and unconfirmed.json()["code"] == "CONFIRM_REQUIRED"
    assert (
        await admin.post("/api/admin/validation", json={"confirm": True, "advanced_password": "sai"})
    ).status_code == 403

    stub.gate.clear()
    try:
        started = await admin.post("/api/admin/validation", json=OK)
        assert started.status_code == 200 and started.json()["status"] == "running" and started.json()["by"] == "admin"
        again = await admin.post("/api/admin/validation", json=OK)
        assert again.status_code == 409 and again.json()["code"] == "SYSTEM_BUSY"
        order_id = (await staff.post("/api/orders")).json()["id"]
        busy = await staff.post(f"/api/orders/{order_id}/captures", content=_jpeg())
        assert busy.status_code == 503 and busy.json()["code"] == "SYSTEM_BUSY"
        tested = await admin.post("/api/admin/evidence-test", content=_jpeg())
        assert tested.status_code == 503 and tested.json()["code"] == "SYSTEM_BUSY"
    finally:
        stub.gate.set()
    deadline = time.monotonic() + 10
    while (await admin.get("/api/admin/validation")).json()["status"] == "running" and time.monotonic() < deadline:
        await asyncio.sleep(0.02)
    done = (await admin.get("/api/admin/validation")).json()
    assert done["status"] == "done" and done["result"] == {"f1": 0.5, "fusion_accuracy": 0.6}
    assert done["finished_at"] and done["report_dir"]
    assert (await staff.post(f"/api/orders/{order_id}/captures", content=_jpeg())).status_code == 202
    assert (await staff.get("/api/admin/validation")).status_code == 403


async def test_evidence_test_reports_ocr_hits_colour_and_barcode(
    admin: httpx.AsyncClient, staff: httpx.AsyncClient, stub: StubRecognizer, tmp_path: Path
) -> None:
    evidence = {
        "ocr": {"text": "hop ngoai ABA 200g"},
        "color": {"dominant_rgb": [198, 160, 150]},
        "barcode": {"barcodes": [{"data": "8931000001372"}]},
    }
    stub.script = lambda _path: Recognition(
        [Detection("7", (10, 10, 100, 120), "uncertain", evidence)], 50.0, detected_count=2
    )
    response = await admin.post("/api/admin/evidence-test", content=_jpeg())
    assert response.status_code == 200
    body = response.json()
    assert body["detected_count"] == 2 and body["processing_time_ms"] == 50.0
    assert body["items"] == [
        {
            "product_id": "7",
            "name": "Kem ABA",
            "status": "uncertain",
            "bbox": [10, 10, 100, 120],
            "ocr_text": "hop ngoai ABA 200g",
            "ocr_keyword_hits": ["7"],
            "color_hex": "#C6A096",
            "color_nearest": {"code": "BE203", "rgb_distance": 2.0},
            "barcodes": ["8931000001372"],
            "barcode_skus": ["1"],
            "plugins": ["barcode", "color", "ocr"],
        }
    ]
    assert list((tmp_path / "media" / "_test").iterdir()) == []  # nothing kept
    bad = await admin.post("/api/admin/evidence-test", content=b"khong phai anh" * 10)
    assert bad.status_code == 400 and bad.json()["code"] == "IMAGE_DECODE_ERROR"
    assert (await staff.post("/api/admin/evidence-test", content=_jpeg())).status_code == 403
