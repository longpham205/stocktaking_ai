"""FastAPI dependency for the audit service."""

from fastapi import Depends

from app.core.backends import Backends
from app.core.deps import backends, require
from app.modules.audit.service import AuditService


def audit_service(b: Backends = Depends(backends)) -> AuditService:
    return require(b.audit, "Nhật ký thay đổi")
