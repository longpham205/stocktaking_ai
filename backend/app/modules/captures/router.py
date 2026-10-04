"""`POST /api/orders/{id}/captures`, `GET /api/jobs/{id}`, and the signed media files
(`GET /api/media/...`, public: whoever holds a link the API signed)."""

import re

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import FileResponse

from app.core.backends import Backends
from app.core.deps import backends
from app.core.errors import AppError, Invalid, NotFound
from app.core.signed_url import verify
from app.modules.auth.deps import current_user
from app.modules.auth.ports import CurrentUser
from app.modules.captures.deps import captures_service
from app.modules.captures.schemas import JobOut, SubmitOut
from app.modules.captures.service import CapturesService

router = APIRouter(tags=["captures"])

# `<order id>/<file>`: what the server names; anything else is not a media path
_MEDIA_PATH = re.compile(r"^\d+/[A-Za-z0-9_.\-]+$")


def _too_large() -> AppError:
    return AppError("Ảnh quá lớn", code="IMAGE_TOO_LARGE", status_code=413)


async def _read_limited(request: Request, limit: int) -> bytes:
    """The raw body, refused as soon as it is known to exceed `limit`."""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise _too_large()
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > limit:
            raise _too_large()
    return bytes(body)


@router.post("/orders/{order_id}/captures", status_code=202)
async def submit_capture(
    order_id: int,
    request: Request,
    idempotency_key: str | None = Header(None),
    current: CurrentUser = Depends(current_user),
    service: CapturesService = Depends(captures_service),
) -> SubmitOut:
    data = await _read_limited(request, service.settings.max_upload_bytes)
    if not data:
        raise Invalid("Thiếu ảnh")
    return await service.submit(current, order_id, data, idempotency_key)


@router.get("/jobs/{job_id}", response_model_exclude_none=True)
async def job(
    job_id: int, current: CurrentUser = Depends(current_user), service: CapturesService = Depends(captures_service)
) -> JobOut:
    return await service.job(current, job_id)


@router.get("/media/{rel_path:path}", include_in_schema=False)
async def media(rel_path: str, exp: int = 0, sig: str = "", b: Backends = Depends(backends)) -> FileResponse:
    if not _MEDIA_PATH.match(rel_path) or not verify(b.settings.media_url_secret, rel_path, exp, sig):
        raise AppError("Liên kết ảnh không hợp lệ hoặc đã hết hạn", code="FORBIDDEN", status_code=403)
    root = b.settings.media_dir.resolve()
    target = (root / rel_path).resolve()
    if root not in target.parents or not target.is_file():
        raise NotFound("Không thấy ảnh")
    return FileResponse(target, headers={"Cache-Control": "private, max-age=300"})
