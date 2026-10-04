"""Accounts and work shifts.

A shift is one login session of one account. An account has at most one open shift: logging in
somewhere else closes the previous one, which is what invalidates its token. The partial unique
index makes that a database guarantee rather than a convention.
"""

from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, String, Text
from sqlalchemy import text as sql
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base
from app.core.columns import MONEY, TIMESTAMP, created_at_column, id_column


class UserRow(Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("role IN ('staff', 'admin')", name="role"),)

    id: Mapped[int] = id_column()
    username: Mapped[str] = mapped_column(String(32), unique=True)
    # scrypt, the legacy format: accounts imported from the old database keep their passwords
    password_hash: Mapped[str] = mapped_column(Text)
    full_name: Mapped[str] = mapped_column(String(80), server_default="")
    role: Mapped[str] = mapped_column(String(16), server_default="staff")
    is_active: Mapped[bool] = mapped_column(server_default=sql("true"))
    has_seen_onboarding: Mapped[bool] = mapped_column(server_default=sql("false"))
    created_at: Mapped[datetime] = created_at_column()


class ShiftRow(Base):
    __tablename__ = "shifts"
    __table_args__ = (
        # one open shift per account
        Index("ux_shifts_user_id_open", "user_id", unique=True, postgresql_where=sql("ended_at IS NULL")),
    )

    id: Mapped[int] = id_column()
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    started_at: Mapped[datetime] = created_at_column()
    ended_at: Mapped[datetime | None] = mapped_column(TIMESTAMP)
    total_collected: Mapped[int] = mapped_column(MONEY, server_default="0")
