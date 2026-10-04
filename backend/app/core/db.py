"""The Postgres connection pool and the timestamp format the API speaks.

The schema is owned by Alembic (migrations/), never created at startup: `make migrate` on the host,
the `migrate` job in docker compose.
"""

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine


def now_iso() -> str:
    return iso(datetime.now(UTC))


def iso(value: datetime) -> str:
    """ISO-8601 in UTC with seconds and an explicit offset: the one timestamp format of the API."""
    return value.astimezone(UTC).isoformat(timespec="seconds")


def sync_database_url(url: str) -> str:
    """The same database through the synchronous driver (psycopg). The engine's catalog code is
    synchronous SQLModel; it shares the database, not the connection pool, with the API."""
    return url.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)


def make_engine(url: str, pool_size: int = 5, max_overflow: int = 5) -> AsyncEngine:
    # pre-ping: a connection the server closed (restart, idle timeout) is replaced, not handed out
    return create_async_engine(url, pool_size=pool_size, max_overflow=max_overflow, pool_pre_ping=True)
