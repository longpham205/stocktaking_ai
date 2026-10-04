"""`/api/auth/*` and `/api/me*`. Login and logout are the only public routes besides health."""

from fastapi import APIRouter, Depends, Request

from app.modules.auth.deps import auth_service, bearer_token, current_user
from app.modules.auth.ports import CurrentUser
from app.modules.auth.schemas import LoginIn, LoginOut, MeOut, OkOut
from app.modules.auth.service import AuthService

router = APIRouter(tags=["auth"])


@router.post("/auth/login")
async def login(body: LoginIn, request: Request, service: AuthService = Depends(auth_service)) -> LoginOut:
    ip = request.client.host if request.client else "unknown"
    return await service.login(body.username, body.password, ip)


@router.post("/auth/logout")
async def logout(token: str | None = Depends(bearer_token), service: AuthService = Depends(auth_service)) -> OkOut:
    await service.logout(token)
    return OkOut()


@router.get("/me")
async def me(current: CurrentUser = Depends(current_user), service: AuthService = Depends(auth_service)) -> MeOut:
    return await service.me(current)


@router.post("/me/onboarding-seen")
async def onboarding_seen(
    current: CurrentUser = Depends(current_user), service: AuthService = Depends(auth_service)
) -> OkOut:
    await service.mark_onboarding_seen(current)
    return OkOut()
