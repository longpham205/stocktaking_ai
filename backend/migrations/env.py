"""Alembic environment: the app's DATABASE_URL, diffing app.core.base.metadata.

Every module that owns tables is imported here so they register on the metadata; forgetting one
means autogenerate would emit a DROP TABLE for it.

A caller may hand in a URL (`config.attributes["database_url"]`): the tests migrate their own
database that way; otherwise it is the process settings'.
"""

import asyncio

from alembic import context
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.base import metadata
from app.core.config import get_settings
from app.modules.audit import models as _audit  # noqa: F401  change_log
from app.modules.auth import models as _auth  # noqa: F401  users, shifts
from app.modules.captures import models as _captures  # noqa: F401  captures
from app.modules.catalog import models as _catalog  # noqa: F401  product_prices
from app.modules.engine_config import models as _engine_config  # noqa: F401  config_overrides
from app.modules.orders import models as _orders  # noqa: F401  orders, order_items
from app.modules.pos_settings import models as _pos_settings  # noqa: F401  settings

target_metadata = metadata


def _url() -> str:
    return context.config.attributes.get("database_url") or get_settings().database_url


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def _run_sync(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(_url())
    async with engine.connect() as connection:
        await connection.run_sync(_run_sync)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
