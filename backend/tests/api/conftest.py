"""Fixtures for the web API tests: the app built with the fake recognizer, on its own database.

The environment is set before any `app` import, so no test reaches the dev database or loads the
engine, whatever the developer's `.env` says.
"""

import os

# A database of its own next to the dev one (`make docker-up-data`), so a run never touches dev data.
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://stocktaking:stocktaking@127.0.0.1:5437/stocktaking_test"
)
os.environ["RECOGNIZER"] = "fake"
os.environ["APP_ENV"] = "test"

import asyncio  # noqa: E402
import tempfile  # noqa: E402
from collections.abc import AsyncIterator, Iterator  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any  # noqa: E402

import httpx  # noqa: E402
import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from app.core.config import CoreSettings  # noqa: E402
from app.core.db import sync_database_url  # noqa: E402
from app.main import create_app  # noqa: E402
from app.modules.auth.config import AuthSettings  # noqa: E402
from app.modules.auth.passwords import hash_password  # noqa: E402
from app.modules.captures.config import CapturesSettings  # noqa: E402
from app.modules.pos_settings.config import PosSettings  # noqa: E402
from app.modules.recognition.ports import RecognizerPort  # noqa: E402

BACKEND_DIR = Path(__file__).resolve().parents[2]
# every web table, for TRUNCATE between tests (the engine's catalog tables: `catalog_url`)
CATALOG_TABLES = "product_evidence, product, color_reference, catalog_meta"
WEB_TABLES = "change_log, captures, order_items, orders, shifts, users, product_prices, settings, config_overrides"
STAFF_PASSWORD, ADMIN_PASSWORD = "staff-pass-123", "admin-pass-456"
# capture photos of a test run, never backend/data (a test that looks at them passes its own)
TEST_MEDIA_DIR = Path(tempfile.mkdtemp(prefix="stocktaking-test-media-"))


def alembic_config(url: str) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    config.attributes["database_url"] = url
    return config


async def _recreate_database(url: str) -> None:
    """Drop and create the test database, through the server's maintenance database."""
    target = make_url(url)
    admin = create_async_engine(target.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        async with admin.connect() as conn:
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{target.database}" WITH (FORCE)'))
            await conn.execute(text(f'CREATE DATABASE "{target.database}"'))
    except OSError as exc:
        raise RuntimeError(
            f"Postgres is not reachable at {target.render_as_string(hide_password=True)}: "
            "start it with `make docker-up-data`"
        ) from exc
    finally:
        await admin.dispose()


def settings(**overrides: Any) -> CoreSettings:
    values: dict[str, Any] = {
        "app_env": "test",
        "recognizer": "fake",
        "database_url": TEST_DATABASE_URL,
        "media_url_secret": "test-media-secret",
        "media_dir": TEST_MEDIA_DIR,
    }
    return CoreSettings(_env_file=None, **{**values, **overrides})  # type: ignore[call-arg]


def auth_settings(**overrides: Any) -> AuthSettings:
    values: dict[str, Any] = {
        "jwt_secret": "test-jwt-secret-0123456789abcdef-0123456789",
        "login_max_failed_attempts": 3,
    }
    return AuthSettings(_env_file=None, **{**values, **overrides})  # type: ignore[call-arg]


def pos_settings(**overrides: Any) -> PosSettings:
    return PosSettings(_env_file=None, **overrides)  # type: ignore[call-arg]


def captures_settings(**overrides: Any) -> CapturesSettings:
    return CapturesSettings(_env_file=None, **overrides)  # type: ignore[call-arg]


@pytest.fixture(scope="session")
def migrated_database_url() -> str:
    """A fresh test database built by the migrations themselves, down and up once, so a broken
    downgrade fails the suite too."""
    asyncio.run(_recreate_database(TEST_DATABASE_URL))
    config = alembic_config(TEST_DATABASE_URL)
    command.upgrade(config, "head")
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    return TEST_DATABASE_URL


@pytest.fixture
async def api_app() -> AsyncIterator[FastAPI]:
    """The app with its lifespan run (httpx's ASGI transport does not run it), no database needed."""
    app = create_app(
        settings(), auth_settings=auth_settings(), pos_settings=pos_settings(), captures_settings=captures_settings()
    )
    async with app.router.lifespan_context(app):
        yield app


@pytest.fixture
async def database_url(migrated_database_url: str) -> str:
    """The migrated test database with every web table emptied."""
    engine = create_async_engine(migrated_database_url)
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {WEB_TABLES} RESTART IDENTITY CASCADE"))
    await engine.dispose()
    return migrated_database_url


@pytest.fixture
def catalog_url(migrated_database_url: str) -> Iterator[str]:
    """The migrated test database through the engine's synchronous driver, catalog tables emptied."""
    from engine.catalog.db import make_engine_from_url

    url = sync_database_url(migrated_database_url)
    engine = make_engine_from_url(url)
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {CATALOG_TABLES} RESTART IDENTITY CASCADE"))
    engine.dispose()
    yield url


async def start_app(
    database_url: str,
    recognizer: RecognizerPort | None = None,
    captures: CapturesSettings | None = None,
    core: dict[str, Any] | None = None,
    **auth_overrides: Any,
) -> FastAPI:
    """An app on the test database, not yet started: enter `app.router.lifespan_context(app)`."""
    return create_app(
        settings(database_url=database_url, **(core or {})),
        recognizer=recognizer,
        auth_settings=auth_settings(**auth_overrides),
        pos_settings=pos_settings(),
        captures_settings=captures or captures_settings(),
    )


async def seed_accounts(app: FastAPI) -> None:
    """The two accounts the legacy web seeded: `staff` and `admin`, with the test passwords."""
    repo = app.state.backends.auth.repo
    await repo.create_user("staff", hash_password(STAFF_PASSWORD), "staff", "Thu ngân demo")
    await repo.create_user("admin", hash_password(ADMIN_PASSWORD), "admin", "Quản trị")


@pytest.fixture
async def db_app(database_url: str) -> AsyncIterator[FastAPI]:
    """The app on an emptied test database, with the `staff` and `admin` accounts."""
    app = await start_app(database_url)
    async with app.router.lifespan_context(app):
        await seed_accounts(app)
        yield app


def http(app: FastAPI, token: str | None = None) -> httpx.AsyncClient:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", headers=headers)


async def login(app: FastAPI, username: str, password: str) -> httpx.Response:
    async with http(app) as client:
        return await client.post("/api/auth/login", json={"username": username, "password": password})


async def token_for(app: FastAPI, username: str, password: str) -> str:
    response = await login(app, username, password)
    assert response.status_code == 200, response.text
    return str(response.json()["token"])


@pytest.fixture
async def client(api_app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=api_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        yield http
