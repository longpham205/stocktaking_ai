"""What one API process is wired to. `app.main._build` fills it once at startup; routers read it
through `app.core.deps`. A backend left as None is a capability this process does not have, and
`deps.require` turns a request for it into a 503 instead of an AttributeError."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import CoreSettings
from app.modules.auth.service import AuthService
from app.modules.recognition.ports import RecognizerPort

Closer = Callable[[], Awaitable[None]]


@dataclass
class Backends:
    settings: CoreSettings
    engine: AsyncEngine
    recognizer: RecognizerPort | None = None
    auth: AuthService | None = None
    # run in reverse order at shutdown
    closers: list[Closer] = field(default_factory=list)
