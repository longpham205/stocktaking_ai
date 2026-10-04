"""Phase 1: the skeleton answers, renders errors in one shape, and reaches its database."""

from datetime import UTC, datetime

import httpx
import pytest
from fastapi import FastAPI

from app.core import signed_url
from app.core.clock import local_date, local_midnight_utc
from app.core.config import BACKEND_DIR, CoreSettings
from app.core.deps import require
from app.core.errors import AppError, NotFound, Unavailable
from app.main import create_app
from tests.api.conftest import settings


async def test_healthz_does_not_need_the_database(client: httpx.AsyncClient) -> None:
    response = await client.get("/healthz")
    assert response.status_code == 200 and response.json() == {"status": "ok"}


async def test_api_health_reports_ready_with_the_fake_recognizer(migrated_database_url: str) -> None:
    app = create_app(settings(database_url=migrated_database_url))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
            response = await http.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ready", "recognizer": "fake"}


async def test_api_health_is_503_when_the_database_is_down() -> None:
    # port 9 (discard): nothing listens there
    down = "postgresql+asyncpg://stocktaking:stocktaking@127.0.0.1:9/stocktaking"
    app = create_app(settings(database_url=down))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
            response = await http.get("/api/health")
    assert response.status_code == 503
    assert response.json()["code"] == "DB_UNAVAILABLE"


async def test_app_errors_render_detail_code_and_extra(api_app: FastAPI) -> None:
    @api_app.get("/api/_boom")
    async def boom() -> None:
        raise AppError("Hệ thống đang bận", code="QUEUE_FULL", status_code=503, retry_after=3)

    @api_app.get("/api/_missing")
    async def missing() -> None:
        raise NotFound("Không thấy đơn hàng")

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api_app), base_url="http://test") as http:
        boom_response, missing_response = await http.get("/api/_boom"), await http.get("/api/_missing")
    assert boom_response.status_code == 503
    assert boom_response.json() == {"detail": "Hệ thống đang bận", "code": "QUEUE_FULL", "retry_after": 3}
    assert missing_response.status_code == 404 and missing_response.json()["code"] == "NOT_FOUND"


async def test_generated_docs_are_served_in_dev_only() -> None:
    for env, expected in (("dev", 200), ("prod", 404)):
        app = create_app(settings(app_env=env))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
            assert (await http.get("/openapi.json")).status_code == expected


def test_require_turns_a_missing_backend_into_503() -> None:
    assert require("x", "Bộ nhận diện") == "x"
    with pytest.raises(Unavailable) as caught:
        require(None, "Bộ nhận diện")
    assert caught.value.status_code == 503 and caught.value.code == "BACKEND_UNAVAILABLE"


def test_settings_resolve_relative_paths_from_backend_dir_and_reject_sync_urls() -> None:
    resolved = settings(data_dir="data_demo", pipeline_config="configs/config.demo.yaml")
    assert resolved.data_dir == BACKEND_DIR / "data_demo"
    assert resolved.pipeline_config == BACKEND_DIR / "configs" / "config.demo.yaml"
    with pytest.raises(ValueError, match="asyncpg"):
        CoreSettings(_env_file=None, database_url="postgresql://u:p@localhost/db")  # type: ignore[call-arg]


def test_signed_url_round_trip_expiry_and_tampering() -> None:
    secret, path = "s3cret", "12/capture_ab.jpg"
    signature = signed_url.sign(secret, path, expires=1_000)
    assert signed_url.verify(secret, path, 1_000, signature, now=999)
    assert not signed_url.verify(secret, path, 1_000, signature, now=1_001)  # expired
    assert not signed_url.verify(secret, "12/other.jpg", 1_000, signature, now=999)  # another file
    assert not signed_url.verify("other", path, 1_000, signature, now=999)  # another secret
    assert not signed_url.verify(secret, path, 1_000, "ch\u1eef k\u00fd", now=999)  # not ASCII: refused, not an error
    assert signed_url.signed_query(secret, path, ttl_seconds=60, now=940) == f"exp=1000&sig={signature}"
    with pytest.raises(ValueError, match="MEDIA_URL_SECRET"):
        signed_url.sign("", path, 1_000)


def test_local_day_starts_at_local_midnight() -> None:
    # 2026-10-04 01:30 in Vietnam (+7) is still 2026-10-03 18:30 UTC
    now = datetime(2026, 10, 3, 18, 30, tzinfo=UTC)
    assert local_midnight_utc(7, now=now) == datetime(2026, 10, 3, 17, 0, tzinfo=UTC)
    assert local_midnight_utc(7, days_ago=6, now=now) == datetime(2026, 9, 27, 17, 0, tzinfo=UTC)
    assert local_date(now, 7) == "2026-10-04"
