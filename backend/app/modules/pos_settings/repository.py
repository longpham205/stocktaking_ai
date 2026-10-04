"""Postgres persistence for the shop settings. The only place in the module that runs SQL."""

from collections.abc import Iterable
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.modules.audit.ports import Change
from app.modules.audit.repository import record_changes
from app.modules.pos_settings.models import SettingRow

_S = SettingRow


async def _upsert(conn: AsyncConnection, values: dict[str, Any]) -> None:
    statement = insert(_S).values([{"key": key, "value": value} for key, value in values.items()])
    await conn.execute(
        statement.on_conflict_do_update(index_elements=[_S.key], set_={"value": statement.excluded.value})
    )


class SettingsRepository:
    def __init__(self, engine: AsyncEngine):
        self.engine = engine

    async def values(self, keys: Iterable[str]) -> dict[str, Any]:
        """The stored value of each key that has one; a key nobody has set is absent."""
        async with self.engine.connect() as conn:
            rows = (await conn.execute(select(_S.key, _S.value).where(_S.key.in_(list(keys))))).all()
        return {key: value for key, value in rows}

    async def save(self, values: dict[str, Any], changes: list[Change], changed_by: int) -> None:
        """Store the values and their change-log entries in one transaction."""
        async with self.engine.begin() as conn:
            for change in changes:  # a setting is its own record: record id = field = the key
                await record_changes(conn, "settings", change.field, [change], changed_by)
            await _upsert(conn, values)

    async def put_secret(self, key: str, value: Any) -> None:
        """Store a value without a change-log entry (a password hash has no business in the log)."""
        async with self.engine.begin() as conn:
            await _upsert(conn, {key: value})
