"""Postgres persistence for accounts and shifts. SQLAlchemy Core on a connection, no ORM session.
The only place in the module that runs SQL."""

from typing import Any

from sqlalchemy import RowMapping, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from app.modules.auth.models import ShiftRow, UserRow
from app.modules.auth.ports import Shift, User

_U = UserRow
_S = ShiftRow


def _user(row: RowMapping) -> User:
    return User(
        id=row["id"],
        username=row["username"],
        password_hash=row["password_hash"],
        full_name=row["full_name"],
        role=row["role"],
        is_active=row["is_active"],
        has_seen_onboarding=row["has_seen_onboarding"],
    )


def _shift(row: RowMapping) -> Shift:
    return Shift(
        id=row["id"],
        user_id=row["user_id"],
        started_at=row["started_at"],
        ended_at=row["ended_at"],
        total_collected=row["total_collected"],
    )


class AuthRepository:
    def __init__(self, engine: AsyncEngine):
        self.engine = engine

    async def user_by_username(self, username: str) -> User | None:
        async with self.engine.connect() as conn:
            row = (await conn.execute(select(_U).where(_U.username == username))).mappings().first()
        return _user(row) if row else None

    async def user(self, user_id: int) -> User | None:
        async with self.engine.connect() as conn:
            row = (await conn.execute(select(_U).where(_U.id == user_id))).mappings().first()
        return _user(row) if row else None

    async def shift(self, shift_id: int) -> Shift | None:
        async with self.engine.connect() as conn:
            row = (await conn.execute(select(_S).where(_S.id == shift_id))).mappings().first()
        return _shift(row) if row else None

    async def open_shift(self, user_id: int) -> Shift:
        """Close whatever shift the account has open and start a new one, in one transaction:
        the account never has two open shifts, and the device holding the old token is logged out."""
        async with self.engine.begin() as conn:
            await conn.execute(
                update(_S).where(_S.user_id == user_id, _S.ended_at.is_(None)).values(ended_at=func.now())
            )
            row = (await conn.execute(insert(_S).values(user_id=user_id).returning(_S))).mappings().one()
        return _shift(row)

    async def close_shift(self, shift_id: int) -> None:
        """Idempotent: a shift that is already closed (or does not exist) stays as it is."""
        async with self.engine.begin() as conn:
            await conn.execute(update(_S).where(_S.id == shift_id, _S.ended_at.is_(None)).values(ended_at=func.now()))

    async def mark_onboarding_seen(self, user_id: int) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(update(_U).where(_U.id == user_id).values(has_seen_onboarding=True))

    async def create_user(self, username: str, password_hash: str, role: str, full_name: str = "") -> User:
        async with self.engine.begin() as conn:
            row = (
                (
                    await conn.execute(
                        insert(_U)
                        .values(username=username, password_hash=password_hash, role=role, full_name=full_name)
                        .returning(_U)
                    )
                )
                .mappings()
                .one()
            )
        return _user(row)

    async def set_password(self, user_id: int, password_hash: str, unlock: bool = False) -> int:
        """Replace the password and close the account's open shifts (every device must log in again).
        Returns how many shifts were closed."""
        values: dict[str, Any] = {"password_hash": password_hash}
        if unlock:
            values["is_active"] = True
        async with self.engine.begin() as conn:
            await conn.execute(update(_U).where(_U.id == user_id).values(**values))
            closed = await conn.execute(
                update(_S).where(_S.user_id == user_id, _S.ended_at.is_(None)).values(ended_at=func.now())
            )
        return closed.rowcount
