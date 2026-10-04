"""Overrides of the engine's pipeline config, applied by an admin from the advanced-settings tab.
The YAML keeps the original value; a row here replaces one dotted key (`retrieval.top_k`)."""

from datetime import datetime
from typing import Any

from sqlalchemy import String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base
from app.core.columns import created_at_column


class ConfigOverrideRow(Base):
    __tablename__ = "config_overrides"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[Any] = mapped_column(JSONB)
    updated_by: Mapped[str | None] = mapped_column(String(32))
    updated_at: Mapped[datetime] = created_at_column()
