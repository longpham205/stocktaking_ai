"""`/api/settings`: what the POS screen needs to know about the shop's settings."""

from fastapi import APIRouter, Depends

from app.modules.auth.deps import current_user
from app.modules.pos_settings.deps import settings_service
from app.modules.pos_settings.schemas import SettingsOut
from app.modules.pos_settings.service import SettingsService

router = APIRouter(tags=["settings"], dependencies=[Depends(current_user)])


@router.get("/settings")
async def settings(service: SettingsService = Depends(settings_service)) -> SettingsOut:
    return await service.public()
