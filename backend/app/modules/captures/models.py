"""One uploaded basket photo and the recognition job that processes it.

The job state lives here, not only in memory: `GET /api/jobs/{id}` reads this row, and a capture
left `queued` or `processing` by a restart is marked as an error at startup.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base
from app.core.columns import created_at_column, id_column


class CaptureRow(Base):
    __tablename__ = "captures"
    __table_args__ = (CheckConstraint("job_status IN ('queued', 'processing', 'done', 'error')", name="job_status"),)

    id: Mapped[int] = id_column()
    order_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("orders.id"), index=True)
    job_status: Mapped[str] = mapped_column(String(16), server_default="queued")
    # {"code": "PIPELINE_TIMEOUT", "message": "..."} when job_status = 'error'
    job_error: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    item_count: Mapped[int] = mapped_column(server_default="0")
    processing_time_ms: Mapped[float | None]
    image_path: Mapped[str | None] = mapped_column(Text)
    # one box per detected object: bbox, the order line it became (or none, for a rejected object)
    detections: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = created_at_column()
