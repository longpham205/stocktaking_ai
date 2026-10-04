"""FastAPI dependency for the captures service."""

from fastapi import Depends

from app.core.backends import Backends
from app.core.deps import backends, require
from app.modules.captures.service import CapturesService


def captures_service(b: Backends = Depends(backends)) -> CapturesService:
    return require(b.captures, "Nhận diện ảnh")
