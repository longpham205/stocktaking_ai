"""Errors that carry their HTTP status and a stable machine code, rendered by one handler as
`{"detail": ..., "code": ..., **extra}`. The frontend maps `code` to a message; `detail` is the
Vietnamese text the legacy web showed."""

from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class AppError(Exception):
    status_code = 400
    code = "BAD_REQUEST"

    def __init__(self, detail: str, *, code: str | None = None, status_code: int | None = None, **extra: Any):
        super().__init__(detail)
        self.detail = detail
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code
        self.extra = extra


class NotFound(AppError):
    status_code = 404
    code = "NOT_FOUND"


class Conflict(AppError):
    status_code = 409
    code = "CONFLICT"


class Invalid(AppError):
    status_code = 422
    code = "VALIDATION_ERROR"


class Unavailable(AppError):
    status_code = 503
    code = "UNAVAILABLE"


async def validation_error_handler(_: Request, exc: Exception) -> JSONResponse:
    """A body or query FastAPI rejected, in the same shape as every other error. `errors` keeps
    where and why, for a developer; the user sees `detail`."""
    assert isinstance(exc, RequestValidationError)
    errors = [{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()]
    return JSONResponse(
        {"detail": "Dữ liệu gửi lên không hợp lệ", "code": Invalid.code, "errors": errors},
        status_code=Invalid.status_code,
    )


async def app_error_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppError)
    return JSONResponse({"detail": exc.detail, "code": exc.code, **exc.extra}, status_code=exc.status_code)
