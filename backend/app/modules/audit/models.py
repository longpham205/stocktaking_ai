"""The change log: who changed which field of which record, from what to what. Every admin edit
of a price, a barcode, recognition evidence, a colour reference or an engine override writes one
row per field, and a row can be reverted while the field still holds `new_value`."""

from datetime import datetime

from sqlalchemy import BigInteger, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base
from app.core.columns import created_at_column, id_column


class ChangeLogRow(Base):
    __tablename__ = "change_log"
    # the history of one record, newest first
    __table_args__ = (Index("ix_change_log_table_name_record_id", "table_name", "record_id"),)

    id: Mapped[int] = id_column()
    table_name: Mapped[str] = mapped_column(String(64))
    record_id: Mapped[str] = mapped_column(String(128))
    field_name: Mapped[str] = mapped_column(String(64))
    # values as text (JSON for structured fields); null = the field was empty
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)
    # null = a system change (startup migration), or the account was deleted
    changed_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="SET NULL"))
    changed_at: Mapped[datetime] = created_at_column()
