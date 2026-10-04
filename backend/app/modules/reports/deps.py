"""FastAPI dependency for the reports service."""

from fastapi import Depends

from app.core.backends import Backends
from app.core.deps import backends, require
from app.modules.reports.service import ReportsService


def reports_service(b: Backends = Depends(backends)) -> ReportsService:
    return require(b.reports, "Báo cáo")
