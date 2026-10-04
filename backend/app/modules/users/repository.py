"""Postgres persistence for staff management. The only place in the module that runs SQL.

The tables are the auth module's (`users`, `shifts`): this module manages accounts, auth logs them in.
"""

from typing import Any

from sqlalchemy import exists, func, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.errors import Conflict
from app.modules.auth.models import ShiftRow, UserRow
from app.modules.users.ports import StaffMember

_U, _S = UserRow, ShiftRow
_ONLINE = exists().where(_S.user_id == _U.id, _S.ended_at.is_(None)).label("online")


class UsersRepository:
    def __init__(self, engine: AsyncEngine):
        self.engine = engine

    async def members(self, user_id: int | None = None) -> list[StaffMember]:
        """Every account (or one), oldest first."""
        query = select(_U.id, _U.username, _U.full_name, _U.role, _U.is_active, _ONLINE).order_by(_U.id)
        if user_id is not None:
            query = query.where(_U.id == user_id)
        async with self.engine.connect() as conn:
            rows = (await conn.execute(query)).mappings().all()
        return [StaffMember(**row) for row in rows]

    async def create(self, username: str, password_hash: str, full_name: str, role: str) -> int:
        values = {"username": username, "password_hash": password_hash, "full_name": full_name, "role": role}
        try:
            async with self.engine.begin() as conn:
                created = await conn.execute(insert(_U).values(**values).returning(_U.id))
                return int(created.scalar_one())
        except IntegrityError as exc:
            if "uq_users_username" in str(exc):
                raise Conflict("Tài khoản đã tồn tại", code="USER_EXISTS") from exc
            raise

    async def update(self, user_id: int, values: dict[str, Any], close_shifts: bool) -> None:
        """Change the account; `close_shifts`: its open shift ends too (every device must log in again)."""
        async with self.engine.begin() as conn:
            if values:
                await conn.execute(update(_U).where(_U.id == user_id).values(**values))
            if close_shifts:
                await conn.execute(
                    update(_S).where(_S.user_id == user_id, _S.ended_at.is_(None)).values(ended_at=func.now())
                )
