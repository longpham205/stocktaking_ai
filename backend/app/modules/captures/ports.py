"""What the rest of the app knows about a capture: a plain record, no SQLAlchemy rows."""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Capture:
    id: int
    order_id: int
    # queued | processing | done | error
    job_status: str
    job_error: dict[str, Any] | None
    item_count: int
    # relative to MEDIA_DIR
    image_path: str | None
    warnings: list[dict[str, Any]] | None
