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
from collections.abc import AsyncIterator  # noqa: E402
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
from app.main import create_app  # noqa: E402

BACKEND_DIR = Path(__file__).resolve().parents[2]


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
    }
    return CoreSettings(_env_file=None, **{**values, **overrides})  # type: ignore[call-arg]


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
    app = create_app(settings())
    async with app.router.lifespan_context(app):
        yield app


@pytest.fixture
async def client(api_app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=api_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        yield http
