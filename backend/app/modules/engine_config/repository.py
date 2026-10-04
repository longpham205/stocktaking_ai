"""Postgres persistence for the engine-setting overrides. The only place in the module that runs SQL."""

import json
from typing import Any

from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine

from app.modules.audit.ports import Change
from app.modules.audit.repository import record_changes
from app.modules.engine_config.models import ConfigOverrideRow

_O = ConfigOverrideRow


def stored_overrides_sync(sync_database_url: str) -> dict[str, Any]:
    """The overrides, read synchronously: the real recognizer is built with them at startup (before
    the event loop serves anything)."""
    engine = create_engine(sync_database_url)
    try:
        with engine.connect() as conn:
            return {key: value for key, value in conn.execute(select(_O.key, _O.value)).all()}
    finally:
        engine.dispose()


def _json(value: Any) -> str:
    return json.dumps(value)


class EngineConfigRepository:
    def __init__(self, engine: AsyncEngine):
        self.engine = engine

    async def overrides(self) -> dict[str, Any]:
        async with self.engine.connect() as conn:
            rows = (await conn.execute(select(_O.key, _O.value))).all()
        return {key: value for key, value in rows}

    async def save(self, old: dict[str, Any], new: dict[str, Any], username: str, user_id: int) -> None:
        """Store `new` in place of `old`, one change-log entry per key that changed, in one transaction."""
        async with self.engine.begin() as conn:
            for key in sorted(set(old) | set(new)):
                if (key in old) == (key in new) and old.get(key) == new.get(key):
                    continue
                if key in new:
                    statement = insert(_O).values(key=key, value=new[key], updated_by=username)
                    await conn.execute(
                        statement.on_conflict_do_update(
                            index_elements=[_O.key],
                            set_={"value": statement.excluded.value, "updated_by": username, "updated_at": func.now()},
                        )
                    )
                else:
                    await conn.execute(delete(_O).where(_O.key == key))
                change = Change(key, _json(old[key]) if key in old else None, _json(new[key]) if key in new else None)
                await record_changes(conn, "config", key, [change], user_id)
