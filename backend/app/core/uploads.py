"""Raw image uploads (a basket photo, an evidence test): the body is the image, not JSON."""

from fastapi import Request

from app.core.errors import AppError


def _too_large() -> AppError:
    return AppError("Ảnh quá lớn", code="IMAGE_TOO_LARGE", status_code=413)


async def read_limited(request: Request, limit: int) -> bytes:
    """The raw body, refused (413 IMAGE_TOO_LARGE) as soon as it is known to exceed `limit`."""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise _too_large()
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > limit:
            raise _too_large()
    return bytes(body)
