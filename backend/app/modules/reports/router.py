"""`/api/admin/reports`: admins only."""

from fastapi import APIRouter, Depends

from app.modules.auth.deps import require_admin
from app.modules.reports.deps import reports_service
from app.modules.reports.schemas import ReportOut
from app.modules.reports.service import ReportsService

router = APIRouter(tags=["reports"])


@router.get("/admin/reports", dependencies=[Depends(require_admin)])
async def report(
    range: str = "today",  # noqa: A002  the legacy query parameter's name
    service: ReportsService = Depends(reports_service),
) -> ReportOut:
    return await service.report(range)
