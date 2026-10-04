"""FastAPI dependencies over the process's `Backends`."""

from typing import cast

from fastapi import Request

from app.core.backends import Backends
from app.core.errors import Unavailable


def backends(request: Request) -> Backends:
    return cast(Backends, request.app.state.backends)


def require[T](value: T | None, what: str) -> T:
    """The backend, or a 503 naming what this process was started without."""
    if value is None:
        raise Unavailable(f"{what} chưa sẵn sàng trên máy chủ này", code="BACKEND_UNAVAILABLE")
    return value
