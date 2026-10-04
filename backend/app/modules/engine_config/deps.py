"""FastAPI dependency for the engine-config service."""

from fastapi import Depends

from app.core.backends import Backends
from app.core.deps import backends, require
from app.modules.engine_config.service import EngineConfigService


def engine_config_service(b: Backends = Depends(backends)) -> EngineConfigService:
    return require(b.engine_config, "Thiết lập nâng cao")
