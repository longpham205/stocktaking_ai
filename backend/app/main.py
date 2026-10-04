"""The composition root: the only place that decides which database and which recognizer a process
uses. Everything else depends on the contracts (`Backends`, `RecognizerPort`), not on these
implementations."""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI
from fastapi.exceptions import RequestValidationError
from sqlalchemy import text

from app.core.backends import Backends
from app.core.config import CoreSettings, get_settings
from app.core.db import make_engine, sync_database_url
from app.core.deps import backends
from app.core.errors import AppError, Unavailable, app_error_handler, validation_error_handler
from app.core.logging import RequestLog, configure_logging
from app.modules.audit.repository import AuditRepository
from app.modules.audit.router import router as audit_router
from app.modules.audit.service import AuditService
from app.modules.auth.config import AuthSettings, get_auth_settings
from app.modules.auth.limiter import LoginLimiter
from app.modules.auth.repository import AuthRepository
from app.modules.auth.router import router as auth_router
from app.modules.auth.service import AuthService
from app.modules.captures.config import CapturesSettings, get_captures_settings
from app.modules.captures.repository import CapturesRepository
from app.modules.captures.router import router as captures_router
from app.modules.captures.service import CapturesService
from app.modules.catalog.repository import CatalogEdits, CatalogRepository
from app.modules.catalog.router import router as catalog_router
from app.modules.catalog.service import CatalogService
from app.modules.engine_config.repository import EngineConfigRepository, stored_overrides_sync
from app.modules.engine_config.router import router as engine_config_router
from app.modules.engine_config.service import EngineConfigService
from app.modules.orders.repository import OrdersRepository
from app.modules.orders.router import router as orders_router
from app.modules.orders.service import OrdersService
from app.modules.pos_settings.config import PosSettings, get_pos_settings
from app.modules.pos_settings.repository import SettingsRepository
from app.modules.pos_settings.router import router as settings_router
from app.modules.pos_settings.service import SettingsService
from app.modules.recognition.ports import RecognizerPort
from app.modules.recognition.worker import RecognitionWorker
from app.modules.reports.repository import ReportsRepository
from app.modules.reports.router import router as reports_router
from app.modules.reports.service import ReportsService
from app.modules.users.repository import UsersRepository
from app.modules.users.router import router as users_router
from app.modules.users.service import UsersService
from app.modules.validation.router import router as validation_router
from app.modules.validation.service import ValidationService

logger = logging.getLogger("app.main")


def build_recognizer(settings: CoreSettings) -> RecognizerPort:
    if settings.recognizer == "fake":
        from app.modules.recognition.fake import FakeRecognizer

        return FakeRecognizer(database_url=sync_database_url(settings.database_url))
    # lazy: torch, faiss and the engine are imported only by a process that runs the real pipeline
    from app.modules.recognition.local_pipeline import LocalRecognizer

    url = sync_database_url(settings.database_url)
    # with the engine settings an admin applied earlier (config_overrides): they survive a restart
    return LocalRecognizer(settings.pipeline_config, url, stored_overrides_sync(url))


def _build(
    settings: CoreSettings,
    recognizer: RecognizerPort | None,
    auth_settings: AuthSettings,
    pos_settings: PosSettings,
    captures_settings: CapturesSettings,
) -> Backends:
    engine = make_engine(settings.database_url, settings.db_pool_size, settings.db_max_overflow)
    built = Backends(settings=settings, engine=engine)
    built.closers.append(engine.dispose)
    if not auth_settings.jwt_secret:
        # nobody could log in: stop at startup instead of failing every login with a 500
        raise RuntimeError("JWT_SECRET is not set: run `make setup` (it fills the secrets in .env)")
    built.auth = AuthService(AuthRepository(engine), auth_settings)
    # the advanced password is locked out like a login: same limits, its own counter
    advanced_limiter = LoginLimiter(auth_settings.login_max_failed_attempts, auth_settings.login_lockout_minutes * 60)
    built.pos_settings = SettingsService(SettingsRepository(engine), pos_settings, advanced_limiter)
    edits = CatalogEdits(sync_database_url(settings.database_url))

    async def close_edits() -> None:
        await asyncio.to_thread(edits.close)

    built.closers.append(close_edits)
    built.catalog = CatalogService(CatalogRepository(engine), edits, settings)
    built.audit = AuditService(AuditRepository(engine), built.catalog)
    built.audit.register("settings", built.pos_settings.revert)
    built.audit.register("product", built.catalog.revert_product)
    built.audit.register("product_evidence", built.catalog.revert_evidence)
    built.audit.register("color_reference", built.catalog.revert_color)
    built.orders = OrdersService(OrdersRepository(engine), built.catalog, built.pos_settings, settings)
    built.recognizer = recognizer or build_recognizer(settings)
    built.worker = RecognitionWorker(
        built.recognizer,
        captures_settings.recognition_queue_max,
        captures_settings.recognition_timeout_seconds,
    )
    built.captures = CapturesService(
        CapturesRepository(engine), built.orders, built.pos_settings, built.worker, settings, captures_settings
    )
    built.users = UsersService(UsersRepository(engine))
    built.reports = ReportsService(
        ReportsRepository(engine), built.catalog, built.worker, settings.timezone_offset_hours
    )
    built.engine_config = EngineConfigService(
        EngineConfigRepository(engine), built.pos_settings, built.worker, settings
    )
    built.audit.register("config", built.engine_config.revert)
    built.validation = ValidationService(built.worker, built.catalog, built.pos_settings, settings)
    # the recognizer re-reads the catalog after an admin edit, between two photos
    built.catalog.on_change(built.worker.reload_catalog)
    # stopped before the engine is disposed (closers run in reverse)
    built.closers.append(built.worker.close)
    return built


api = APIRouter()


@api.get("/health")
async def health(b: Backends = Depends(backends)) -> dict[str, str]:
    """Ready to serve: the database answers. `{"status": "ready"}` is the legacy contract."""
    try:
        async with b.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:
        logger.warning("database not reachable", extra={"error": str(exc)})
        raise Unavailable("Cơ sở dữ liệu chưa sẵn sàng", code="DB_UNAVAILABLE") from exc
    return {"status": "ready", "recognizer": b.recognizer.name if b.recognizer else "none"}


def create_app(
    settings: CoreSettings | None = None,
    recognizer: RecognizerPort | None = None,
    auth_settings: AuthSettings | None = None,
    pos_settings: PosSettings | None = None,
    captures_settings: CapturesSettings | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    auth_settings = auth_settings or get_auth_settings()
    pos_settings = pos_settings or get_pos_settings()
    captures_settings = captures_settings or get_captures_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging(settings.log_level, settings.log_format)
        # the schema is Alembic's (`make migrate`, compose's `migrate` job), never created here
        built = _build(settings, recognizer, auth_settings, pos_settings, captures_settings)
        app.state.backends = built
        assert built.worker is not None and built.captures is not None
        built.worker.start(built.captures.process)
        logger.info("ready", extra={"env": settings.app_env, "recognizer": settings.recognizer})
        yield
        for close in reversed(built.closers):
            await close()
        if built.recognizer is not None:
            built.recognizer.close()

    # generated API docs only in dev: demos expose the API through a public tunnel
    dev = settings.app_env == "dev"
    app = FastAPI(
        title="Stocktaking AI POS",
        version="0.2.0",
        lifespan=lifespan,
        docs_url="/docs" if dev else None,
        redoc_url=None,
        openapi_url="/openapi.json" if dev else None,
    )
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        """Alive: the process answers. Does not touch the database (docker healthcheck)."""
        return {"status": "ok"}

    app.include_router(api, prefix="/api")
    app.include_router(auth_router, prefix="/api")
    app.include_router(settings_router, prefix="/api")
    app.include_router(catalog_router, prefix="/api")
    app.include_router(orders_router, prefix="/api")
    app.include_router(captures_router, prefix="/api")
    app.include_router(audit_router, prefix="/api")
    app.include_router(users_router, prefix="/api")
    app.include_router(reports_router, prefix="/api")
    app.include_router(engine_config_router, prefix="/api")
    app.include_router(validation_router, prefix="/api")
    app.add_middleware(RequestLog)
    return app
