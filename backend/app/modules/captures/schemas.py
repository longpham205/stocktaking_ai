"""Response bodies of the capture routes (field names are the legacy API's)."""

from typing import Any

from pydantic import BaseModel

from app.modules.orders.schemas import OrderOut


class SubmitOut(BaseModel):
    job_id: int
    # true: the same Idempotency-Key was sent a moment ago; this is that job
    duplicate: bool


class JobOut(BaseModel):
    # queued | processing | done | error
    status: str
    # captures ahead of this one in the queue
    position: int
    # an admin is applying new engine settings: the wait is longer (phase 4)
    system_reloading: bool = False
    # when done: objects added, what to tell the cashier, the order afterwards
    added: int | None = None
    warnings: list[dict[str, Any]] | None = None
    order: OrderOut | None = None
    # when error: {"code", "message"}
    error: dict[str, Any] | None = None
