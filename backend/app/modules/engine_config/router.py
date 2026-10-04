"""`/api/admin/config*`: admins only; applying needs the advanced password too."""

from fastapi import APIRouter, Depends

from app.modules.auth.deps import require_admin
from app.modules.auth.ports import CurrentUser
from app.modules.engine_config.deps import engine_config_service
from app.modules.engine_config.schemas import ApplyIn, ConfigOut
from app.modules.engine_config.service import EngineConfigService

router = APIRouter(tags=["engine-config"])


@router.get("/admin/config", dependencies=[Depends(require_admin)])
async def config(service: EngineConfigService = Depends(engine_config_service)) -> ConfigOut:
    return await service.view()


@router.post("/admin/config/apply")
async def apply_config(
    body: ApplyIn,
    admin: CurrentUser = Depends(require_admin),
    service: EngineConfigService = Depends(engine_config_service),
) -> ConfigOut:
    return await service.apply(admin, body)
