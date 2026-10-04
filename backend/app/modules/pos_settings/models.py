"""Shop settings an admin changes at runtime (thresholds per capture, tilt blocking, receipt
printing, the advanced-password hash). One row per key; the value is whatever JSON the key holds."""

from typing import Any

from sqlalchemy import String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base


class SettingRow(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Any] = mapped_column(JSONB)
