"""Request and response bodies of the auth routes (field names are the legacy API's)."""

from pydantic import BaseModel


class LoginIn(BaseModel):
    username: str = ""
    password: str = ""


class UserOut(BaseModel):
    id: int
    username: str
    full_name: str
    role: str
    has_seen_onboarding: bool


class LoginOut(BaseModel):
    token: str
    user: UserOut


class ShiftOut(BaseModel):
    id: int
    started_at: str
    total_collected: int


class MeOut(BaseModel):
    user: UserOut
    shift: ShiftOut


class OkOut(BaseModel):
    ok: bool = True
