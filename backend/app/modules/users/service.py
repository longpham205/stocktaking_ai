"""Staff management by admins: list accounts, create one, rename, reset a password, lock or unlock."""

import asyncio
import re
from dataclasses import asdict
from typing import Any

from app.core.errors import Conflict, Invalid, NotFound
from app.modules.auth.passwords import hash_password
from app.modules.auth.ports import CurrentUser
from app.modules.users.repository import UsersRepository
from app.modules.users.schemas import StaffCreate, StaffOut, StaffPatch

MIN_PASSWORD = 8
MAX_FULL_NAME = 80
_USERNAME = re.compile(r"^[a-zA-Z0-9_.\-]{3,32}$")


def _password(value: str | None) -> str:
    if value is None or len(value) < MIN_PASSWORD:
        raise Invalid(f"Mật khẩu tối thiểu {MIN_PASSWORD} ký tự")
    return value


class UsersService:
    def __init__(self, repo: UsersRepository):
        self.repo = repo

    async def members(self) -> list[StaffOut]:
        return [StaffOut(**asdict(m)) for m in await self.repo.members()]

    async def _member(self, user_id: int) -> StaffOut:
        found = await self.repo.members(user_id)
        if not found:
            raise NotFound("Không thấy nhân viên")
        return StaffOut(**asdict(found[0]))

    async def create(self, body: StaffCreate) -> StaffOut:
        username = body.username.strip().lower()
        if not _USERNAME.match(username):
            raise Invalid("Tài khoản 3–32 ký tự (chữ, số, . _ -)")
        password = _password(body.password)
        if body.role not in ("staff", "admin"):
            raise Invalid("Vai trò phải là staff hoặc admin")
        # scrypt is deliberately slow: off the event loop
        password_hash = await asyncio.to_thread(hash_password, password)
        user_id = await self.repo.create(username, password_hash, body.full_name[:MAX_FULL_NAME], body.role)
        return await self._member(user_id)

    async def update(self, current: CurrentUser, user_id: int, body: StaffPatch) -> StaffOut:
        await self._member(user_id)
        sent = body.model_fields_set
        values: dict[str, Any] = {}
        close_shifts = False
        if "password" in sent:
            values["password_hash"] = await asyncio.to_thread(hash_password, _password(body.password))
            close_shifts = True
        if "full_name" in sent:
            values["full_name"] = (body.full_name or "")[:MAX_FULL_NAME]
        if "is_active" in sent:
            if body.is_active is None:
                raise Invalid("is_active phải là true/false")
            if user_id == current.user_id and not body.is_active:
                raise Conflict("Không thể tự khoá tài khoản của mình", code="VALIDATION_ERROR")
            values["is_active"] = body.is_active
            close_shifts = close_shifts or not body.is_active
        await self.repo.update(user_id, values, close_shifts)
        return await self._member(user_id)
