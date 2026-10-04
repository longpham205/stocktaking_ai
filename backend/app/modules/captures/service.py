"""Basket photos: accept an upload, queue it for recognition, turn the result into order lines.

A job is the capture row: its id is the job id and its `job_status` the job's state, so a job
survives a page reload. A capture still pending in the database but unknown to this process's
worker was cut off by a restart, and is reported as such when asked for.
"""

import asyncio
import logging
import time
import uuid
from pathlib import Path
from typing import Any

from app.core.config import CoreSettings
from app.core.errors import AppError, NotFound
from app.modules.auth.ports import CurrentUser
from app.modules.captures.config import CapturesSettings
from app.modules.captures.repository import CapturesRepository
from app.modules.captures.schemas import JobOut, SubmitOut
from app.modules.orders.service import OrdersService, merge_or_insert
from app.modules.pos_settings.service import SettingsService
from app.modules.recognition.mapper import Line, is_image, merge_detections, open_upright, save_thumbnail
from app.modules.recognition.ports import Box, Recognition
from app.modules.recognition.worker import RecognitionWorker

logger = logging.getLogger("app.captures")

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
PENDING = ("queued", "processing")


def _error(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def _warnings(result: Recognition) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    if result.has_overlap:
        warnings.append({"type": "overlap_detected"})
    if result.detected_count is not None and result.detected_count > len(result.detections):
        # the pipeline drops the objects it could not recognise: the cashier should know they are there
        warnings.append({"type": "unrecognized_objects", "count": result.detected_count - len(result.detections)})
    return warnings


def _pixels(box: Box) -> list[int]:
    return [round(float(v)) for v in box]


class CapturesService:
    def __init__(
        self,
        repo: CapturesRepository,
        orders: OrdersService,
        pos_settings: SettingsService,
        worker: RecognitionWorker,
        core: CoreSettings,
        settings: CapturesSettings,
    ):
        self.repo, self.orders, self.pos_settings, self.worker = repo, orders, pos_settings, worker
        self.media_dir, self.settings = core.media_dir, settings
        # (order id, Idempotency-Key) -> (capture id, when): one process, in memory
        self._idempotency: dict[tuple[int, str], tuple[int, float]] = {}

    # ---------------------------------------------------------------- upload

    def _recent(self, order_id: int, key: str | None) -> int | None:
        now = time.monotonic()
        window = self.settings.idempotency_window_seconds
        self._idempotency = {k: v for k, v in self._idempotency.items() if now - v[1] < window}
        hit = self._idempotency.get((order_id, key)) if key else None
        return hit[0] if hit else None

    def _save(self, order_id: int, data: bytes) -> str:
        """Write the photo under a name the server picks. Returns its path relative to MEDIA_DIR."""
        ext = ".png" if data.startswith(PNG_MAGIC) else ".jpg"
        rel = f"{order_id}/capture_{uuid.uuid4().hex[:10]}{ext}"
        target = self.media_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return rel

    async def submit(self, current: CurrentUser, order_id: int, data: bytes, key: str | None) -> SubmitOut:
        if self.worker.validating:  # a validation holds the recognizer for many minutes: say so at once
            raise AppError(
                "Hệ thống đang kiểm định độ chính xác, tạm thời chưa nhận diện được — thêm món thủ công hoặc thử lại sau",
                code="SYSTEM_BUSY",
                status_code=503,
            )
        await self.orders.check_open(current, order_id)
        key = (key or "").strip()[:64] or None
        if (duplicate := self._recent(order_id, key)) is not None:
            return SubmitOut(job_id=duplicate, duplicate=True)
        if self.worker.full:
            raise AppError("Hệ thống đang bận, thử lại sau ít giây", code="QUEUE_FULL", status_code=503)
        if not await asyncio.to_thread(is_image, data):
            raise AppError("Không giải mã được ảnh", code="IMAGE_DECODE_ERROR", status_code=400)
        rel = await asyncio.to_thread(self._save, order_id, data)
        async with self.repo.write() as unit:
            capture_id = await unit.create(order_id, rel)
        try:
            self.worker.submit(capture_id)
        except AppError as exc:  # filled up while the photo was being saved
            async with self.repo.write() as unit:
                await unit.update(capture_id, job_status="error", job_error=_error(exc.code, exc.detail))
            raise
        if key:
            self._idempotency[(order_id, key)] = (capture_id, time.monotonic())
        return SubmitOut(job_id=capture_id, duplicate=False)

    # ---------------------------------------------------------------- job state

    async def job(self, current: CurrentUser, job_id: int) -> JobOut:
        # asked before the database is read: a job the worker finishes in between is then still
        # seen as known, never mistaken for one a restart cut off
        known = self.worker.knows(job_id)
        async with self.repo.read() as unit:
            capture = await unit.capture(job_id)
        if capture is None:
            raise NotFound("Không thấy tác vụ")
        try:
            order = await self.orders.get(current, capture.order_id)
        except NotFound:
            raise NotFound("Không thấy tác vụ") from None  # someone else's job: not revealed
        status, error = capture.job_status, capture.job_error
        if status in PENDING and not known:
            status, error = "error", _error("SERVER_RESTARTED", "Máy chủ đã khởi động lại, hãy chụp lại")
            async with self.repo.write() as unit:
                await unit.update(capture.id, job_status=status, job_error=error, only_if_status=PENDING)
        out = JobOut(
            status=status,
            position=self.worker.position(capture.id) if status == "queued" else 0,
            # an admin is applying engine settings: the wait is longer than usual
            system_reloading=self.worker.reloading,
        )
        if status == "done":
            out.added, out.warnings, out.order = capture.item_count, capture.warnings or [], order
        elif status == "error":
            out.error = error
        return out

    # ---------------------------------------------------------------- the job itself

    async def process(self, capture_id: int) -> None:
        """Run by the worker, one capture at a time."""
        async with self.repo.write() as unit:
            capture = await unit.capture(capture_id)
            if capture is None or capture.image_path is None:
                return
            await unit.update(capture_id, job_status="processing")
        settings = await self.pos_settings.public()
        path = self.media_dir / capture.image_path
        started = time.perf_counter()
        try:
            result = await self.worker.recognize(
                str(path), settings.similarity_threshold, settings.min_confidence_accept
            )
        except TimeoutError:
            logger.error("recognition timed out", extra={"capture_id": capture_id})
            await self._fail(
                capture_id,
                _error("PIPELINE_TIMEOUT", "Xử lý quá lâu, hãy chụp lại"),
                self.worker.timeout_seconds * 1000,
            )
            return
        except Exception as exc:
            logger.exception("recognition failed", extra={"capture_id": capture_id})
            code = "GPU_OOM" if "out of memory" in str(exc).lower() else "PIPELINE_ERROR"
            elapsed_ms = (time.perf_counter() - started) * 1000
            await self._fail(capture_id, _error(code, "Không xử lý được ảnh, hãy chụp lại"), elapsed_ms)
            return
        elapsed_ms = result.processing_time_ms or (time.perf_counter() - started) * 1000
        lines = merge_detections(result.detections)
        size, thumbs = await asyncio.to_thread(self._thumbnails, path, capture.order_id, lines)
        await self._apply(capture_id, capture.order_id, result, lines, thumbs, size, elapsed_ms)

    async def _fail(self, capture_id: int, error: dict[str, str], elapsed_ms: float) -> None:
        async with self.repo.write() as unit:
            await unit.update(capture_id, job_status="error", job_error=error, processing_time_ms=elapsed_ms)

    def _thumbnails(
        self, path: Path, order_id: int, lines: list[Line]
    ) -> tuple[tuple[int, int] | None, list[str | None]]:
        """The photo's size and one thumbnail per line (None where the box is unusable)."""
        image = open_upright(path)
        if image is None:
            return None, [None] * len(lines)
        thumbs: list[str | None] = []
        for line in lines:
            rel = f"{order_id}/t_{uuid.uuid4().hex[:10]}.jpg"
            saved = save_thumbnail(image, line.bboxes[0], self.media_dir / rel, self.settings.thumb_width)
            thumbs.append(rel if saved else None)
        return image.size, thumbs

    async def _apply(
        self,
        capture_id: int,
        order_id: int,
        result: Recognition,
        lines: list[Line],
        thumbs: list[str | None],
        size: tuple[int, int] | None,
        elapsed_ms: float,
    ) -> None:
        """The recognised lines join the order, and the capture records its boxes, together."""
        async with self.repo.write() as unit:
            order = await unit.orders.order(order_id, lock=True)
            if order is None or order.status != "open":
                error = _error("ORDER_NOT_OPEN", "Đơn hàng đã đóng")
                await unit.update(capture_id, job_status="error", job_error=error, processing_time_ms=elapsed_ms)
                return
            added, boxes = 0, []
            for line, thumb in zip(lines, thumbs, strict=True):
                if line.flagged:  # an uncertain object is always a line of its own
                    item_id = await unit.orders.insert_item(
                        order_id, line.product_id, 1, flagged=True, thumb_path=thumb, evidence=line.evidence
                    )
                else:
                    item_id = await merge_or_insert(
                        unit.orders, order_id, line.product_id, line.quantity, thumb_path=thumb, evidence=line.evidence
                    )
                status = "uncertain" if line.flagged else "accepted"
                for bbox in line.bboxes:
                    boxes.append(
                        {"item_id": item_id, "product_id": line.product_id, "status": status, "bbox": _pixels(bbox)}
                    )
                added += line.quantity
            for bbox in result.rejected_bboxes:  # found but not recognised: a red box, no line
                boxes.append({"item_id": None, "product_id": None, "status": "rejected", "bbox": _pixels(bbox)})
            await unit.update(
                capture_id,
                job_status="done",
                item_count=added,
                processing_time_ms=elapsed_ms,
                warnings=_warnings(result),
                detections=boxes if size else None,
                image_width=size[0] if size else None,
                image_height=size[1] if size else None,
            )
