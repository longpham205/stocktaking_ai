"""Postgres persistence for the shop settings. The only place in the module that runs SQL."""

from collections.abc import Iterable
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from app.modules.pos_settings.models import SettingRow

_S = SettingRow


class SettingsRepository:
    def __init__(self, engine: AsyncEngine):
        self.engine = engine

    async def values(self, keys: Iterable[str]) -> dict[str, Any]:
        """The stored value of each key that has one; a key nobody has set is absent."""
        async with self.engine.connect() as conn:
            rows = (await conn.execute(select(_S.key, _S.value).where(_S.key.in_(list(keys))))).all()
        return {key: value for key, value in rows}
