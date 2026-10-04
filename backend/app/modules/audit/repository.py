"""Postgres persistence for the change log. The only place in the module that runs SQL.

`record_changes` runs on the caller's connection: a module logs its edit inside the transaction that
makes it, so the data and its log entry commit or roll back together.
"""

from collections.abc import Iterable
from typing import Any

from sqlalchemy import Connection, RowMapping, insert, select
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.modules.audit.models import ChangeLogRow
from app.modules.audit.ports import Change, ChangeEntry
from app.modules.auth.models import UserRow

_L = ChangeLogRow
# every entry with the username of whoever made it (a statement is immutable: shared safely)
_SELECT = select(_L, UserRow.username).outerjoin(UserRow, UserRow.id == _L.changed_by)


def _rows(table: str, record_id: str, changes: Iterable[Change], changed_by: int | None) -> list[dict[str, Any]]:
    return [
        {
            "table_name": table,
            "record_id": record_id,
            "field_name": change.field,
            "old_value": change.old,
            "new_value": change.new,
            "changed_by": changed_by,
        }
        for change in changes
    ]


async def record_changes(
    conn: AsyncConnection, table: str, record_id: str, changes: Iterable[Change], changed_by: int | None
) -> None:
    if rows := _rows(table, record_id, changes, changed_by):
        await conn.execute(insert(_L), rows)


def record_changes_sync(
    conn: Connection, table: str, record_id: str, changes: Iterable[Change], changed_by: int | None
) -> None:
    """The same on a synchronous connection: the catalog writes go through the engine's
    synchronous session (`catalog.repository.CatalogEdits`)."""
    if rows := _rows(table, record_id, changes, changed_by):
        conn.execute(insert(_L), rows)


def _entry(row: RowMapping) -> ChangeEntry:
    return ChangeEntry(
        id=row["id"],
        table=row["table_name"],
        record_id=row["record_id"],
        field=row["field_name"],
        old=row["old_value"],
        new=row["new_value"],
        by=row["username"],
        at=row["changed_at"],
    )


class AuditRepository:
    def __init__(self, engine: AsyncEngine):
        self.engine = engine

    async def entries(self, table: str, record_id: str, limit: int) -> list[ChangeEntry]:
        """Newest first, optionally of one table and one record."""
        query = _SELECT
        if table:
            query = query.where(_L.table_name == table)
        if record_id:
            query = query.where(_L.record_id == record_id)
        async with self.engine.connect() as conn:
            rows = (await conn.execute(query.order_by(_L.id.desc()).limit(limit))).mappings().all()
        return [_entry(row) for row in rows]

    async def entry(self, entry_id: int) -> ChangeEntry | None:
        async with self.engine.connect() as conn:
            row = (await conn.execute(_SELECT.where(_L.id == entry_id))).mappings().first()
        return _entry(row) if row else None
