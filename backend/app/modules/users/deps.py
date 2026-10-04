"""FastAPI dependency for the users service."""

from fastapi import Depends

from app.core.backends import Backends
from app.core.deps import backends, require
from app.modules.users.service import UsersService


def users_service(b: Backends = Depends(backends)) -> UsersService:
    return require(b.users, "Quản lý nhân viên")
