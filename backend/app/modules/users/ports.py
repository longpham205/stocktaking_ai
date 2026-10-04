"""What the rest of the app knows about a staff account as admins manage it."""

from dataclasses import dataclass


@dataclass(frozen=True)
class StaffMember:
    id: int
    username: str
    full_name: str
    role: str
    is_active: bool
    # has an open shift (logged in somewhere)
    online: bool
