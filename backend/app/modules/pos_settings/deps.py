"""FastAPI dependency for the settings service."""

from fastapi import Depends

from app.core.backends import Backends
from app.core.deps import backends, require
from app.modules.pos_settings.service import SettingsService


def settings_service(b: Backends = Depends(backends)) -> SettingsService:
    return require(b.pos_settings, "Thiết lập")
