"""The shop settings: what every logged-in account reads (stored values over the process defaults),
what an admin changes (each change in the change log), and the advanced password that guards the
engine settings."""

import asyncio
import json
from typing import Any

from app.core.errors import AppError, Conflict, Invalid
from app.modules.audit.ports import Change, ChangeEntry
from app.modules.audit.schemas import RevertIn
from app.modules.auth.limiter import LoginLimiter
from app.modules.auth.passwords import verify_password
from app.modules.auth.ports import CurrentUser
from app.modules.pos_settings.config import PosSettings
from app.modules.pos_settings.repository import SettingsRepository
from app.modules.pos_settings.schemas import FLAGS, THRESHOLD_BOUNDS, SettingsOut, SettingsPatch

PUBLIC_KEYS = tuple(SettingsOut.model_fields)
ADVANCED_PASSWORD_KEY = "advanced_password_hash"


class SettingsService:
    def __init__(self, repo: SettingsRepository, defaults: PosSettings, limiter: LoginLimiter):
        self.repo, self.defaults, self.limiter = repo, defaults, limiter

    async def public(self) -> SettingsOut:
        stored = await self.repo.values(PUBLIC_KEYS)
        return SettingsOut(
            allow_checkout_without_price=bool(
                stored.get("allow_checkout_without_price", self.defaults.allow_checkout_without_price)
            ),
            similarity_threshold=stored.get("similarity_threshold"),
            min_confidence_accept=stored.get("min_confidence_accept"),
            tilt_block_capture=bool(stored.get("tilt_block_capture", False)),
            auto_print_receipt=bool(stored.get("auto_print_receipt", False)),
        )

    async def update(self, current: CurrentUser, body: SettingsPatch) -> SettingsOut:
        sent = body.model_fields_set
        changes: dict[str, Any] = {}
        for key in FLAGS:
            if key in sent:
                if getattr(body, key) is None:
                    raise Invalid(f"{key} phải là true/false")
                changes[key] = getattr(body, key)
        for key, (low, high) in THRESHOLD_BOUNDS.items():
            if key in sent:
                value = getattr(body, key)
                if value is not None and not low <= value <= high:
                    raise Invalid(f"{key} phải trong {low:.2f}..{high:.2f} hoặc null")
                changes[key] = value
        if not changes:
            raise Invalid("Không có thiết lập hợp lệ nào")
        old = (await self.public()).model_dump()
        log = [Change(key, json.dumps(old[key]), json.dumps(value)) for key, value in changes.items()]
        await self.repo.save(changes, log, current.user_id)
        return await self.public()

    async def revert(self, current: CurrentUser, entry: ChangeEntry, _: RevertIn) -> None:
        """Change-log reverter for the `settings` table."""
        current_value = (await self.public()).model_dump().get(entry.field)
        if entry.new is None or current_value != json.loads(entry.new):
            raise Conflict("Cài đặt đã được thay đổi sau lần sửa này, không thể hoàn tác", code="CHANGE_STALE")
        old = None if entry.old is None else json.loads(entry.old)
        await self.update(current, SettingsPatch.model_validate({entry.field: old}))

    async def advanced_password_set(self) -> bool:
        return bool((await self.repo.values([ADVANCED_PASSWORD_KEY])).get(ADVANCED_PASSWORD_KEY))

    async def verify_advanced_password(self, current: CurrentUser, password: str | None) -> None:
        """The advanced password (not the login one) guards the engine settings. Locked for a while
        after repeated wrong tries, per account."""
        key = ("advanced", str(current.user_id))
        if wait := self.limiter.retry_after(key):
            raise AppError(
                "Nhập sai mật khẩu nâng cao quá nhiều lần, thử lại sau",
                code="RATE_LIMITED",
                status_code=429,
                retry_after=wait,
            )
        stored = (await self.repo.values([ADVANCED_PASSWORD_KEY])).get(ADVANCED_PASSWORD_KEY)
        if not stored:
            raise Conflict(
                "Chưa có mật khẩu nâng cao — chạy trên máy chủ: make reset-advanced-password",
                code="ADVANCED_PASSWORD_NOT_SET",
            )
        if not password or not await asyncio.to_thread(verify_password, password, stored):
            self.limiter.fail(key)
            raise AppError("Sai mật khẩu nâng cao", code="ADVANCED_PASSWORD_INVALID", status_code=403)
        self.limiter.reset(key)
