"""FastAPI dependency for the validation service."""

from fastapi import Depends

from app.core.backends import Backends
from app.core.deps import backends, require
from app.modules.validation.service import ValidationService


def validation_service(b: Backends = Depends(backends)) -> ValidationService:
    return require(b.validation, "Kiểm định")
