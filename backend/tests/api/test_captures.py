"""Basket photos: upload, the recognition queue, the job state, order lines from boxes, signed media.
Ported from the legacy suite (src_legacy/tests/test_backend_api.py), with a scripted recognizer."""

import asyncio
import io
import threading
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pytest
from fastapi import FastAPI
from PIL import Image, ImageDraw
from sqlalchemy import insert, select
from sqlmodel import Session

from app.modules.auth.passwords import hash_password
from app.modules.captures.models import CaptureRow
from app.modules.recognition.fake import FakeRecognizer
from app.modules.recognition.mapper import json_safe, merge_detections
from app.modules.recognition.ports import Detection, Recognition
from engine.catalog.db import Product, make_engine_from_url
from tests.api.conftest import (
    ADMIN_PASSWORD,
    STAFF_PASSWORD,
    captures_settings,
    http,
    seed_accounts,
    start_app,
    token_for,
)

Script = Callable[[str], Recognition]
BOX = (20, 20, 160, 210)


def _basket(_path: str) -> Recognition:
    """Two of 7 and one 8 recognised, one more 7 the recognizer is not sure about."""
    return Recognition(
        [Detection("7", BOX, "accepted"), Detection("7", BOX, "accepted"), Detection("8", BOX, "accepted")]
        + [Detection("7", BOX, "uncertain")],
        123.0,
    )


class ScriptedRecognizer:
    name = "scripted"

    def __init__(self, script: Script):
        self.script = script

    def recognize(
        self, image_path: str, similarity_threshold: float | None = None, min_confidence_accept: float | None = None
    ) -> Recognition:
        return self.script(image_path)

    def reload_catalog(self) -> None:
        return None

    def close(self) -> None:
        return None


def _jpeg(color: tuple[int, int, int] = (40, 120, 200)) -> bytes:
    image = Image.new("RGB", (320, 240), (90, 90, 90))
    ImageDraw.Draw(image).rectangle((30, 30, 150, 200), fill=color)
    out = io.BytesIO()
    image.save(out, "JPEG")
    return out.getvalue()


@pytest.fixture
def catalog(catalog_url: str) -> None:
    engine = make_engine_from_url(catalog_url)
    with Session(engine) as session:
        session.add(Product(product_id="7", product_name="Kem dưỡng da"))
        session.add(Product(product_id="8", product_name="Sữa rửa mặt"))
        session.commit()
    engine.dispose()


MakeApp = Callable[..., Awaitable[FastAPI]]


@pytest.fixture
async def make_app(database_url: str, catalog: None, tmp_path: Path) -> AsyncIterator[MakeApp]:
    """Start an app with a scripted recognizer, its photos under tmp_path; stopped after the test."""
    async with AsyncExitStack() as stack:

        async def make(script: Script = _basket, **captures: Any) -> FastAPI:
            app = await start_app(
                database_url,
                recognizer=ScriptedRecognizer(script),
                captures=captures_settings(**captures),
                core={"media_dir": tmp_path},
            )
            await stack.enter_async_context(app.router.lifespan_context(app))
            await seed_accounts(app)
            return app

        yield make


async def _staff(app: FastAPI) -> httpx.AsyncClient:
    return http(app, await token_for(app, "staff", STAFF_PASSWORD))


async def _capture(
    client: httpx.AsyncClient, order_id: int, key: str | None = None, data: bytes | None = None
) -> httpx.Response:
    headers = {"Idempotency-Key": key} if key else {}
    return await client.post(f"/api/orders/{order_id}/captures", content=data or _jpeg(), headers=headers)


async def _wait(client: httpx.AsyncClient, job_id: int, want: tuple[str, ...] = ("done", "error")) -> dict[str, Any]:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        response = await client.get(f"/api/jobs/{job_id}")
        assert response.status_code == 200, response.text
        body: dict[str, Any] = response.json()
        if body["status"] in want:
            return body
        await asyncio.sleep(0.02)
    raise AssertionError("the job did not finish in time")


async def _order_with_capture(client: httpx.AsyncClient) -> tuple[int, dict[str, Any]]:
    order_id = (await client.post("/api/orders")).json()["id"]
    response = await _capture(client, order_id)
    assert response.status_code == 202, response.text
    return order_id, await _wait(client, response.json()["job_id"])


# ---------------------------------------------------------------- over HTTP


async def test_capture_flow_merge_rule_thumbnails_and_signed_media(make_app: MakeApp) -> None:
    app = await make_app()
    async with await _staff(app) as staff, http(app) as anonymous:
        order_id, job = await _order_with_capture(staff)
        assert job["status"] == "done" and job["added"] == 4 and job["warnings"] == [] and job["position"] == 0
        assert "error" not in job
        order = job["order"]
        # accepted objects of one product share a line; the uncertain one stays apart, even for 7
        assert [(i["product_id"], i["quantity"], i["flagged"]) for i in order["items"]] == [
            ("7", 2, False),
            ("8", 1, False),
            ("7", 1, True),
        ]
        assert order["flagged_count"] == 1 and order["item_count"] == 4
        url = order["items"][0]["thumbnail_url"]
        assert url.startswith("/api/media/")
        image = await anonymous.get(url)  # signed: no token needed
        assert image.status_code == 200 and image.content[:2] == b"\xff\xd8"
        assert Image.open(io.BytesIO(image.content)).width == 240
        for bad in (
            url[:-3] + "AAA",
            f"/api/media/{order_id}/x.jpg?exp=1&sig=abc",
            "/api/media/../../etc/passwd?exp=9999999999&sig=x",
        ):
            assert (await anonymous.get(bad)).status_code in (403, 404)
        assert (await anonymous.get(url[:-3] + "AAA")).json()["code"] == "FORBIDDEN"


async def test_order_view_has_the_photo_and_boxes_linked_to_lines(make_app: MakeApp) -> None:
    app = await make_app()
    async with await _staff(app) as staff, http(app) as anonymous:
        order_id, _ = await _order_with_capture(staff)
        order = (await staff.get(f"/api/orders/{order_id}")).json()
        (capture,) = order["captures"]
        assert (capture["width"], capture["height"]) == (320, 240) and len(capture["boxes"]) == 4
        items = {i["id"]: i for i in order["items"]}
        accepted7 = [b for b in capture["boxes"] if b["product_id"] == "7" and b["status"] == "accepted"]
        assert len(accepted7) == 2 and accepted7[0]["item_id"] == accepted7[1]["item_id"]
        assert items[accepted7[0]["item_id"]]["quantity"] == 2
        (uncertain,) = [b for b in capture["boxes"] if b["status"] == "uncertain"]
        assert items[uncertain["item_id"]]["flagged"] and uncertain["bbox"] == list(BOX)
        photo = await anonymous.get(capture["image_url"])
        assert photo.status_code == 200 and len(photo.content) > 500

        # another photo: accepted objects add up on the confirmed lines, each uncertain one is a new line
        job = await _wait(staff, (await _capture(staff, order_id, key="k2")).json()["job_id"])
        lines = {(i["product_id"], i["flagged"]): i["quantity"] for i in job["order"]["items"]}
        assert lines[("7", False)] == 4 and lines[("8", False)] == 2
        assert sum(1 for i in job["order"]["items"] if i["flagged"]) == 2
        second = job["order"]["captures"][1]["boxes"]
        assert (
            next(b for b in second if b["product_id"] == "7" and b["status"] == "accepted")["item_id"]
            == (accepted7[0]["item_id"])
        )
        # an order with a photo is not empty: a new order is really new
        assert (await staff.post("/api/orders")).json()["id"] != order_id


async def test_same_idempotency_key_returns_the_same_job(make_app: MakeApp) -> None:
    app = await make_app()
    async with await _staff(app) as staff:
        order_id = (await staff.post("/api/orders")).json()["id"]
        first, second = await _capture(staff, order_id, key="abc-123"), await _capture(staff, order_id, key="abc-123")
        assert first.status_code == second.status_code == 202
        assert first.json() == {"job_id": first.json()["job_id"], "duplicate": False}
        assert second.json() == {"job_id": first.json()["job_id"], "duplicate": True}
        await _wait(staff, first.json()["job_id"])
        assert (await staff.get(f"/api/orders/{order_id}")).json()["item_count"] == 4  # processed once


async def test_bad_image_too_large_and_empty_uploads(make_app: MakeApp) -> None:
    app = await make_app(max_upload_mb=0.05)
    async with await _staff(app) as staff:
        order_id = (await staff.post("/api/orders")).json()["id"]
        response = await _capture(staff, order_id, data=b"day khong phai anh" * 50)
        assert response.status_code == 400 and response.json()["code"] == "IMAGE_DECODE_ERROR"
        response = await _capture(staff, order_id, data=b"\x00" * 60_000)
        assert response.status_code == 413 and response.json()["code"] == "IMAGE_TOO_LARGE"
        response = await staff.post(f"/api/orders/{order_id}/captures", content=b"")
        assert response.status_code == 422 and response.json()["code"] == "VALIDATION_ERROR"
        assert (await staff.get("/api/me")).status_code == 200
    async with app.state.backends.engine.connect() as conn:
        assert (await conn.execute(select(CaptureRow.id))).all() == []  # none of them became a capture


async def test_pipeline_error_is_reported_and_adds_nothing(make_app: MakeApp) -> None:
    def boom(_path: str) -> Recognition:
        raise RuntimeError("CUDA out of memory. Tried to allocate")

    app = await make_app(boom)
    async with await _staff(app) as staff:
        order_id = (await staff.post("/api/orders")).json()["id"]
        job = await _wait(staff, (await _capture(staff, order_id)).json()["job_id"])
        assert job["status"] == "error" and job["error"]["code"] == "GPU_OOM" and "order" not in job
        assert (await staff.get(f"/api/orders/{order_id}")).json()["items"] == []
    async with app.state.backends.engine.connect() as conn:
        assert (await conn.execute(select(CaptureRow.job_status))).scalars().all() == ["error"]


async def test_queue_full(make_app: MakeApp) -> None:
    gate = threading.Event()

    def slow(_path: str) -> Recognition:
        gate.wait(10)
        return Recognition([], 1.0)

    app = await make_app(slow, recognition_queue_max=1)
    try:
        async with await _staff(app) as staff:
            order_id = (await staff.post("/api/orders")).json()["id"]
            first = (await _capture(staff, order_id)).json()["job_id"]
            await _wait(staff, first, want=("processing",))
            queued = await _capture(staff, order_id)  # waits in the queue (size 1)
            assert queued.status_code == 202
            assert (await staff.get(f"/api/jobs/{queued.json()['job_id']}")).json()["status"] == "queued"
            refused = await _capture(staff, order_id)
            assert refused.status_code == 503 and refused.json()["code"] == "QUEUE_FULL"
            gate.set()
            assert (await _wait(staff, queued.json()["job_id"]))["status"] == "done"
    finally:
        gate.set()


async def test_a_job_that_hangs_times_out_and_the_queue_recovers(make_app: MakeApp) -> None:
    calls = {"n": 0}

    def hangs_once(_path: str) -> Recognition:
        calls["n"] += 1
        if calls["n"] == 1:
            time.sleep(1.5)
        return Recognition([Detection("7", BOX, "accepted")], 5.0)

    app = await make_app(hangs_once, recognition_timeout_seconds=0.4)
    async with await _staff(app) as staff:
        order_id = (await staff.post("/api/orders")).json()["id"]
        started = time.monotonic()
        job = await _wait(staff, (await _capture(staff, order_id)).json()["job_id"])
        assert job["status"] == "error" and job["error"]["code"] == "PIPELINE_TIMEOUT"
        assert time.monotonic() - started < 1.2  # reported at once, not after 1.5 s
        # once the hung recognition has finished in the background, the queue works again
        await asyncio.sleep(1.3)
        job = await _wait(staff, (await _capture(staff, order_id)).json()["job_id"])
        assert job["status"] == "done" and job["order"]["item_count"] == 1


async def test_warnings_overlap_and_unrecognised_objects_with_their_red_boxes(make_app: MakeApp) -> None:
    script: dict[str, Any] = {"overlap": True, "count": None, "rejected": []}

    def scripted(_path: str) -> Recognition:
        return Recognition(
            [Detection("7", (10, 10, 100, 120), "accepted")],
            5.0,
            has_overlap=script["overlap"],
            detected_count=script["count"],
            rejected_bboxes=script["rejected"],
        )

    app = await make_app(scripted)
    async with await _staff(app) as staff:
        order_id = (await staff.post("/api/orders")).json()["id"]

        async def warnings() -> list[dict[str, Any]]:
            job = await _wait(staff, (await _capture(staff, order_id)).json()["job_id"])
            assert job["status"] == "done", job
            return list(job["warnings"])

        assert await warnings() == [{"type": "overlap_detected"}]
        script.update(overlap=False, count=6)
        assert await warnings() == [{"type": "unrecognized_objects", "count": 5}]
        script.update(count=1)
        assert await warnings() == []
        script.update(count=None)  # not reported: no warning, no error
        assert await warnings() == []
        script.update(overlap=True, count=3, rejected=[(150, 20, 200, 90), (210.4, 30, 300, 200)])
        assert await warnings() == [{"type": "overlap_detected"}, {"type": "unrecognized_objects", "count": 2}]
        boxes = (await staff.get(f"/api/orders/{order_id}")).json()["captures"][-1]["boxes"]
        rejected = [b for b in boxes if b["status"] == "rejected"]
        assert rejected == [
            {"item_id": None, "product_id": None, "status": "rejected", "bbox": [150, 20, 200, 90]},
            {"item_id": None, "product_id": None, "status": "rejected", "bbox": [210, 30, 300, 200]},
        ]


async def test_evidence_with_numpy_values_does_not_break_a_capture(make_app: MakeApp) -> None:
    def with_numpy(_path: str) -> Recognition:
        evidence = {"color": {"delta_e": np.float32(3.25), "vec": np.array([1, 2])}, "ocr": {"text": "ABA"}}
        return Recognition([Detection("7", BOX, "uncertain", evidence)], 10.0)

    app = await make_app(with_numpy)
    async with await _staff(app) as staff:
        order_id = (await staff.post("/api/orders")).json()["id"]
        job = await _wait(staff, (await _capture(staff, order_id)).json()["job_id"])
        assert job["status"] == "done", job
        assert job["order"]["items"][0]["evidence"] == {
            "color": {"delta_e": 3.25, "vec": [1, 2]},
            "ocr": {"text": "ABA"},
        }


async def test_closed_orders_and_other_cashiers(make_app: MakeApp) -> None:
    gate = threading.Event()

    def gated(path: str) -> Recognition:
        gate.wait(10)
        return _basket(path)

    app = await make_app(gated)
    try:
        async with await _staff(app) as staff, http(app, await token_for(app, "admin", ADMIN_PASSWORD)) as admin:
            order_id = (await staff.post("/api/orders")).json()["id"]
            job_id = (await _capture(staff, order_id)).json()["job_id"]
            await app.state.backends.auth.repo.create_user("staff2", hash_password("password222"), "staff", "")
            async with http(app, await token_for(app, "staff2", "password222")) as other:
                assert (await other.get(f"/api/jobs/{job_id}")).status_code == 404
                assert (await _capture(other, order_id)).status_code == 404
            assert (await admin.get(f"/api/jobs/{job_id}")).status_code == 200
            # the order is voided while its photo is being recognised: the result is not added
            await _wait(staff, job_id, want=("processing",))
            assert (await staff.post(f"/api/orders/{order_id}/void")).json()["status"] == "void"
            gate.set()
            job = await _wait(staff, job_id)
            assert job["status"] == "error" and job["error"]["code"] == "ORDER_NOT_OPEN"
            assert (await staff.get(f"/api/orders/{order_id}")).json()["items"] == []
            refused = await _capture(staff, order_id)
            assert refused.status_code == 409 and refused.json()["code"] == "ORDER_NOT_OPEN"
            assert (await staff.get("/api/jobs/999999")).status_code == 404
    finally:
        gate.set()


async def test_a_job_left_pending_by_a_restart_is_reported(make_app: MakeApp) -> None:
    app = await make_app()
    async with await _staff(app) as staff:
        order_id = (await staff.post("/api/orders")).json()["id"]
        async with app.state.backends.engine.begin() as conn:
            created = await conn.execute(
                insert(CaptureRow).values(order_id=order_id, job_status="processing").returning(CaptureRow.id)
            )
            job_id = created.scalar_one()
        job = (await staff.get(f"/api/jobs/{job_id}")).json()
        assert job["status"] == "error" and job["error"]["code"] == "SERVER_RESTARTED"
    async with app.state.backends.engine.connect() as conn:
        assert (await conn.execute(select(CaptureRow.job_status))).scalar_one() == "error"


# ---------------------------------------------------------------- units


def test_merge_rule_and_json_safe_evidence() -> None:
    lines = merge_detections(
        [
            Detection("1", (0, 0, 10, 10), "accepted"),
            Detection("1", (10, 0, 20, 10), "uncertain"),
            Detection("1", (20, 0, 30, 10), "accepted"),
            Detection("2", (30, 0, 40, 10), "rejected"),
        ]
    )
    assert [(line.product_id, line.quantity, line.flagged, len(line.bboxes)) for line in lines] == [
        ("1", 2, False, 2),
        ("1", 1, True, 1),
    ]
    assert json_safe({}) is None and json_safe({"big": "x" * 9000}) is None
    assert json_safe({"n": np.int64(3)}) == {"n": 3}


def test_fake_recognizer_is_repeatable_and_empty_without_a_catalog(tmp_path: Path) -> None:
    photo = tmp_path / "basket.jpg"
    photo.write_bytes(_jpeg())
    fake = FakeRecognizer(product_ids=["7", "8", "12"], delay_seconds=0)
    first, again = fake.recognize(str(photo)), fake.recognize(str(photo))
    assert first == again and 3 <= len(first.detections) <= 7
    assert {d.product_id for d in first.detections} <= {"7", "8", "12"}
    assert sum(1 for d in first.detections if d.status == "uncertain") == 1
    assert FakeRecognizer(delay_seconds=0).recognize(str(photo)).detections == []
    (tmp_path / "broken.jpg").write_bytes(b"not an image")
    with pytest.raises(ValueError):
        fake.recognize(str(tmp_path / "broken.jpg"))
