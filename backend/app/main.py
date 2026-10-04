"""The composition root: the only place that decides which database and which recognizer a process
uses. Everything else depends on the contracts (`Backends`, `RecognizerPort`), not on these
implementations."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI
from sqlalchemy import text

from app.core.backends import Backends
from app.core.config import CoreSettings, get_settings
from app.core.db import make_engine, sync_database_url
from app.core.deps import backends
from app.core.errors import AppError, Unavailable, app_error_handler
from app.core.logging import RequestLog, configure_logging
from app.modules.recognition.ports import RecognizerPort

logger = logging.getLogger("app.main")


def build_recognizer(settings: CoreSettings) -> RecognizerPort:
    if settings.recognizer == "fake":
        from app.modules.recognition.fake import FakeRecognizer

        return FakeRecognizer()
    # lazy: torch, faiss and the engine are imported only by a process that runs the real pipeline
    from app.modules.recognition.local_pipeline import LocalRecognizer

    return LocalRecognizer(settings.pipeline_config, sync_database_url(settings.database_url))


def _build(settings: CoreSettings, recognizer: RecognizerPort | None) -> Backends:
    engine = make_engine(settings.database_url, settings.db_pool_size, settings.db_max_overflow)
    built = Backends(settings=settings, engine=engine)
    built.closers.append(engine.dispose)
    built.recognizer = recognizer or build_recognizer(settings)
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


def create_app(settings: CoreSettings | None = None, recognizer: RecognizerPort | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging(settings.log_level, settings.log_format)
        # the schema is Alembic's (`make migrate`, compose's `migrate` job), never created here
        built = _build(settings, recognizer)
        app.state.backends = built
        logger.info("ready", extra={"env": settings.app_env, "recognizer": settings.recognizer})
        yield
        if built.recognizer is not None:
            built.recognizer.close()
        for close in reversed(built.closers):
            await close()

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

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        """Alive: the process answers. Does not touch the database (docker healthcheck)."""
        return {"status": "ok"}

    app.include_router(api, prefix="/api")
    app.add_middleware(RequestLog)
    return app
