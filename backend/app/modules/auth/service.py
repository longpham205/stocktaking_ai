"""Login, logout and the per-request check that a token still names an open shift."""

import asyncio

from app.core.db import iso
from app.core.errors import AppError, Invalid
from app.modules.auth.config import AuthSettings
from app.modules.auth.limiter import LoginLimiter
from app.modules.auth.passwords import DUMMY_HASH, verify_password
from app.modules.auth.ports import CurrentUser, User
from app.modules.auth.repository import AuthRepository
from app.modules.auth.schemas import LoginOut, MeOut, ShiftOut, UserOut
from app.modules.auth.tokens import TokenClaims, TokenError, make_token, verify_token
from app.modules.pos_settings.schemas import SettingsOut


class Unauthorized(AppError):
    status_code = 401
    code = "AUTH_INVALID"


class Forbidden(AppError):
    status_code = 403
    code = "FORBIDDEN"


def _user_out(user: User) -> UserOut:
    return UserOut(
        id=user.id,
        username=user.username,
        full_name=user.full_name,
        role=user.role,
        has_seen_onboarding=user.has_seen_onboarding,
    )


class AuthService:
    def __init__(self, repo: AuthRepository, settings: AuthSettings):
        self.repo, self.settings = repo, settings
        self.limiter = LoginLimiter(settings.login_max_failed_attempts, settings.login_lockout_minutes * 60)

    async def login(self, username: str, password: str, ip: str) -> LoginOut:
        username = username.strip().lower()
        if not username or not password:
            raise Invalid("Thiếu tài khoản hoặc mật khẩu")
        key = (username, ip)
        if wait := self.limiter.retry_after(key):
            raise AppError(
                "Đăng nhập sai quá nhiều lần, thử lại sau", code="RATE_LIMITED", status_code=429, retry_after=wait
            )
        user = await self.repo.user_by_username(username)
        # scrypt is deliberately slow: off the event loop. Always verified, so the time does not
        # tell whether the account exists.
        ok = await asyncio.to_thread(verify_password, password, user.password_hash if user else DUMMY_HASH)
        if user is None or not ok or not user.is_active:
            self.limiter.fail(key)
            # one message for every failure: it does not reveal whether the account exists
            raise Unauthorized("Sai tài khoản hoặc mật khẩu")
        self.limiter.reset(key)
        shift = await self.repo.open_shift(user.id)
        token = make_token(
            self.settings.jwt_secret,
            TokenClaims(user_id=user.id, shift_id=shift.id, role=user.role),
            self.settings.token_ttl_minutes * 60,
        )
        return LoginOut(token=token, user=_user_out(user))

    async def authenticate(self, token: str | None) -> CurrentUser:
        if not token:
            raise Unauthorized("Cần đăng nhập")
        try:
            claims = verify_token(self.settings.jwt_secret, token)
        except TokenError as exc:
            raise Unauthorized("Phiên đăng nhập không hợp lệ hoặc đã hết hạn", code=exc.code) from exc
        user, shift = await self.repo.user(claims.user_id), await self.repo.shift(claims.shift_id)
        if user is None or not user.is_active:
            raise Unauthorized("Tài khoản không hợp lệ")
        if shift is None or shift.ended_at is not None or shift.user_id != user.id:
            raise Unauthorized("Tài khoản đã đăng nhập ở thiết bị khác hoặc ca đã kết thúc", code="SESSION_INVALID")
        return CurrentUser(
            user_id=user.id, role=user.role, shift_id=shift.id, username=user.username, full_name=user.full_name
        )

    async def logout(self, token: str | None) -> None:
        """Idempotent: a missing or broken token, or a shift already closed, is still a success."""
        try:
            claims = verify_token(self.settings.jwt_secret, token or "")
        except TokenError:
            return
        await self.repo.close_shift(claims.shift_id)

    async def me(self, current: CurrentUser, settings: SettingsOut) -> MeOut:
        user, shift = await self.repo.user(current.user_id), await self.repo.shift(current.shift_id)
        assert user is not None and shift is not None  # `current` was just authenticated
        return MeOut(
            user=_user_out(user),
            shift=ShiftOut(id=shift.id, started_at=iso(shift.started_at), total_collected=shift.total_collected),
            settings=settings,
        )

    async def mark_onboarding_seen(self, current: CurrentUser) -> None:
        await self.repo.mark_onboarding_seen(current.user_id)
