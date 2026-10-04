"""What one API process is wired to. `app.main._build` fills it once at startup; routers read it
through `app.core.deps`. A backend left as None is a capability this process does not have, and
`deps.require` turns a request for it into a 503 instead of an AttributeError."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import CoreSettings
from app.modules.audit.service import AuditService
from app.modules.auth.service import AuthService
from app.modules.captures.service import CapturesService
from app.modules.catalog.service import CatalogService
from app.modules.engine_config.service import EngineConfigService
from app.modules.orders.service import OrdersService
from app.modules.pos_settings.service import SettingsService
from app.modules.recognition.ports import RecognizerPort
from app.modules.recognition.worker import RecognitionWorker
from app.modules.reports.service import ReportsService
from app.modules.users.service import UsersService
from app.modules.validation.service import ValidationService

Closer = Callable[[], Awaitable[None]]


@dataclass
class Backends:
    settings: CoreSettings
    engine: AsyncEngine
    recognizer: RecognizerPort | None = None
    auth: AuthService | None = None
    pos_settings: SettingsService | None = None
    audit: AuditService | None = None
    catalog: CatalogService | None = None
    orders: OrdersService | None = None
    worker: RecognitionWorker | None = None
    captures: CapturesService | None = None
    users: UsersService | None = None
    reports: ReportsService | None = None
    engine_config: EngineConfigService | None = None
    validation: ValidationService | None = None
    # run in reverse order at shutdown
    closers: list[Closer] = field(default_factory=list)
