"""Request and response bodies of the change-log routes (field names are the legacy API's)."""

from pydantic import BaseModel


class ChangeOut(BaseModel):
    id: int
    table: str
    record_id: str
    field: str
    old: str | None
    new: str | None
    by: str | None
    at: str
    # the product's name, for a product's entries
    name: str | None


class ChangeLogOut(BaseModel):
    items: list[ChangeOut]


class RevertIn(BaseModel):
    # reverting an engine setting needs the advanced password
    advanced_password: str | None = None


class RevertedOut(BaseModel):
    table: str
    record_id: str
    field: str


class RevertOut(BaseModel):
    ok: bool = True
    reverted: RevertedOut
