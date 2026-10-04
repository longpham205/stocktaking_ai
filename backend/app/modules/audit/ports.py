"""What the rest of the app knows about the change log: plain records, no SQLAlchemy rows."""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Change:
    """One field of one record changed: what is written (`record_changes`)."""

    field: str
    # as text (JSON for structured values); None = the field was empty
    old: str | None
    new: str | None


@dataclass(frozen=True)
class ChangeEntry:
    """One row of the log, as read back."""

    id: int
    table: str
    record_id: str
    field: str
    old: str | None
    new: str | None
    # username; None for a system change or a deleted account
    by: str | None
    at: datetime
