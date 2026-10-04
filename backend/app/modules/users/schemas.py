"""Request and response bodies of the staff routes (field names are the legacy API's)."""

from pydantic import BaseModel, ConfigDict, StrictBool


class StaffOut(BaseModel):
    id: int
    username: str
    full_name: str
    role: str
    is_active: bool
    online: bool


class StaffListOut(BaseModel):
    items: list[StaffOut]


class StaffCreate(BaseModel):
    username: str = ""
    password: str = ""
    full_name: str = ""
    role: str = "staff"


class StaffPatch(BaseModel):
    """Only the fields sent are changed. A new password or a lock ends the account's open shift."""

    model_config = ConfigDict(extra="ignore")

    password: str | None = None
    full_name: str | None = None
    is_active: StrictBool | None = None
