"""`/api/admin/validation` and `/api/admin/evidence-test`: admins only."""

from fastapi import APIRouter, Depends, Request

from app.core.backends import Backends
from app.core.deps import backends
from app.core.errors import Invalid
from app.core.uploads import read_limited
from app.modules.auth.deps import require_admin
from app.modules.auth.ports import CurrentUser
from app.modules.validation.deps import validation_service
from app.modules.validation.schemas import EvidenceTestOut, ValidationIn, ValidationOut
from app.modules.validation.service import ValidationService

router = APIRouter(tags=["validation"])


@router.get("/admin/validation", dependencies=[Depends(require_admin)])
async def validation_status(service: ValidationService = Depends(validation_service)) -> ValidationOut:
    return await service.status()


@router.post("/admin/validation")
async def start_validation(
    body: ValidationIn,
    admin: CurrentUser = Depends(require_admin),
    service: ValidationService = Depends(validation_service),
) -> ValidationOut:
    return await service.start(admin, body)


@router.post("/admin/evidence-test", dependencies=[Depends(require_admin)])
async def evidence_test(
    request: Request, b: Backends = Depends(backends), service: ValidationService = Depends(validation_service)
) -> EvidenceTestOut:
    assert b.captures is not None
    data = await read_limited(request, b.captures.settings.max_upload_bytes)
    if not data:
        raise Invalid("Thiếu ảnh")
    return await service.evidence_test(data)
