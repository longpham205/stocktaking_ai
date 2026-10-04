"""What the rest of the app knows about accounts: plain records, no SQLAlchemy rows."""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class User:
    id: int
    username: str
    password_hash: str
    full_name: str
    role: str
    is_active: bool
    has_seen_onboarding: bool


@dataclass(frozen=True)
class Shift:
    id: int
    user_id: int
    started_at: datetime
    ended_at: datetime | None
    total_collected: int


@dataclass(frozen=True)
class CurrentUser:
    """The authenticated caller of a request: an active account inside its open shift."""

    user_id: int
    role: str
    shift_id: int
    username: str
    full_name: str

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"
