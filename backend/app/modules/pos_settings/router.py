"""`/api/settings` (what the POS screen needs to know) and `/api/admin/settings` (admins change it)."""

from fastapi import APIRouter, Depends

from app.modules.auth.deps import current_user, require_admin
from app.modules.auth.ports import CurrentUser
from app.modules.pos_settings.deps import settings_service
from app.modules.pos_settings.schemas import SettingsOut, SettingsPatch
from app.modules.pos_settings.service import SettingsService

router = APIRouter(tags=["settings"])


@router.get("/settings", dependencies=[Depends(current_user)])
async def settings(service: SettingsService = Depends(settings_service)) -> SettingsOut:
    return await service.public()


@router.patch("/admin/settings")
async def update_settings(
    body: SettingsPatch,
    admin: CurrentUser = Depends(require_admin),
    service: SettingsService = Depends(settings_service),
) -> SettingsOut:
    return await service.update(admin, body)
