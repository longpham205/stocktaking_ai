"""The change log as admins see it, and reverting one change.

A revert writes the old value back through the module that owns the data (so it is validated and
logged like any edit). Each module registers how to revert its own table (`register`, wired in
`app.main`); a change is reverted only while the field still holds the value that change wrote,
otherwise 409 CHANGE_STALE.
"""

from collections.abc import Awaitable, Callable

from app.core.db import iso
from app.core.errors import Invalid, NotFound
from app.modules.audit.ports import ChangeEntry
from app.modules.audit.repository import AuditRepository
from app.modules.audit.schemas import ChangeOut, RevertedOut, RevertIn, RevertOut
from app.modules.auth.ports import CurrentUser
from app.modules.catalog.service import CatalogService

Reverter = Callable[[CurrentUser, ChangeEntry, RevertIn], Awaitable[None]]
# tables whose record id is a product id
_PRODUCT_TABLES = ("product", "product_evidence")


class AuditService:
    def __init__(self, repo: AuditRepository, catalog: CatalogService):
        self.repo, self.catalog = repo, catalog
        self._reverters: dict[str, Reverter] = {}

    def register(self, table: str, reverter: Reverter) -> None:
        self._reverters[table] = reverter

    async def entries(self, table: str = "", record_id: str = "", limit: int = 100) -> list[ChangeOut]:
        entries = await self.repo.entries(table, record_id, max(1, min(500, limit)))
        names = await self.catalog.lookup({e.record_id for e in entries if e.table in _PRODUCT_TABLES})
        return [
            ChangeOut(
                id=e.id,
                table=e.table,
                record_id=e.record_id,
                field=e.field,
                old=e.old,
                new=e.new,
                by=e.by,
                at=iso(e.at),
                name=names[e.record_id].name if e.table in _PRODUCT_TABLES and e.record_id in names else None,
            )
            for e in entries
        ]

    async def revert(self, current: CurrentUser, entry_id: int, body: RevertIn) -> RevertOut:
        entry = await self.repo.entry(entry_id)
        if entry is None:
            raise NotFound("Không thấy dòng nhật ký")
        reverter = self._reverters.get(entry.table)
        if reverter is None:
            raise Invalid("Loại thay đổi này không hoàn tác được")
        await reverter(current, entry, body)
        return RevertOut(reverted=RevertedOut(table=entry.table, record_id=entry.record_id, field=entry.field))
