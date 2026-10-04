"""FastAPI dependencies other modules use to know who is calling:

current: CurrentUser = Depends(current_user)      any logged-in account
admin:   CurrentUser = Depends(require_admin)     admins only (403 FORBIDDEN otherwise)
"""

from fastapi import Depends, Request

from app.core.backends import Backends
from app.core.deps import backends, require
from app.modules.auth.ports import CurrentUser
from app.modules.auth.service import AuthService, Forbidden


def auth_service(b: Backends = Depends(backends)) -> AuthService:
    return require(b.auth, "Đăng nhập")


def bearer_token(request: Request) -> str | None:
    header = request.headers.get("Authorization") or ""
    scheme, _, token = header.partition(" ")
    return token.strip() or None if scheme.lower() == "bearer" else None


async def current_user(
    token: str | None = Depends(bearer_token), service: AuthService = Depends(auth_service)
) -> CurrentUser:
    return await service.authenticate(token)


async def require_admin(current: CurrentUser = Depends(current_user)) -> CurrentUser:
    if not current.is_admin:
        raise Forbidden("Chỉ quản trị viên được thực hiện")
    return current
