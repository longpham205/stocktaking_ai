"""`/api/admin/change-log*`: admins only."""

from fastapi import APIRouter, Depends

from app.modules.audit.deps import audit_service
from app.modules.audit.schemas import ChangeLogOut, RevertIn, RevertOut
from app.modules.audit.service import AuditService
from app.modules.auth.deps import require_admin
from app.modules.auth.ports import CurrentUser

router = APIRouter(tags=["audit"])


@router.get("/admin/change-log", dependencies=[Depends(require_admin)])
async def change_log(
    table: str = "", record: str = "", limit: int = 100, service: AuditService = Depends(audit_service)
) -> ChangeLogOut:
    return ChangeLogOut(items=await service.entries(table, record, limit))


@router.post("/admin/change-log/{entry_id}/revert")
async def revert(
    entry_id: int,
    body: RevertIn | None = None,
    admin: CurrentUser = Depends(require_admin),
    service: AuditService = Depends(audit_service),
) -> RevertOut:
    return await service.revert(admin, entry_id, body or RevertIn())
