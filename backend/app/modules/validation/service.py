"""Two admin tools that use the recognizer itself:

- the benchmark validation, run on the worker thread with the pipeline already loaded (minutes;
  captures are refused meanwhile), compared with the stored baseline;
- the evidence test: one photo through the pipeline (nothing saved), with what each plugin read and
  which catalog entries that matches, to check the evidence an admin is editing.
"""

import asyncio
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.config import CoreSettings
from app.core.db import now_iso
from app.core.errors import AppError
from app.modules.auth.ports import CurrentUser
from app.modules.catalog.service import CatalogService
from app.modules.pos_settings.service import SettingsService
from app.modules.recognition.mapper import is_image
from app.modules.recognition.worker import RecognitionWorker
from app.modules.validation.schemas import (
    EvidenceTestOut,
    NearestColorOut,
    TestedObjectOut,
    ValidationIn,
    ValidationOut,
)


def _gate_metrics(report: dict[str, Any]) -> dict[str, Any]:
    """The figures the release gate compares."""
    return {
        "f1": (report.get("end_to_end") or {}).get("f1"),
        "fusion_accuracy": (report.get("fusion") or {}).get("accuracy_after"),
    }


def _busy() -> AppError:
    return AppError("Hệ thống đang kiểm định, thử lại sau", code="SYSTEM_BUSY", status_code=503)


class ValidationService:
    def __init__(
        self,
        worker: RecognitionWorker,
        catalog: CatalogService,
        pos_settings: SettingsService,
        settings: CoreSettings,
    ):
        self.worker, self.catalog, self.pos_settings, self.settings = worker, catalog, pos_settings, settings
        # the last validation of this process (one process: in memory)
        self._state: dict[str, Any] = {"status": "idle"}

    # ---------------------------------------------------------------- validation

    def _baseline_path(self) -> Path:
        """The engine's baseline report: data/baseline/report.json, or demo/ for the demo config."""
        root = self.settings.pipeline_config.resolve().parent.parent
        name = "demo/report.json" if self.settings.pipeline_config.name == "config.demo.yaml" else "report.json"
        return root / "data" / "baseline" / name

    def _baseline(self) -> dict[str, Any] | None:
        path = self._baseline_path()
        if not path.is_file():
            return None
        return _gate_metrics(json.loads(path.read_text(encoding="utf-8")))

    async def status(self) -> ValidationOut:
        return ValidationOut(**self._state, baseline=await asyncio.to_thread(self._baseline))

    async def start(self, current: CurrentUser, body: ValidationIn) -> ValidationOut:
        if body.confirm is not True:
            raise AppError(
                "Cần xác nhận: hệ thống ngừng nhận diện trong lúc kiểm định (có thể 20–50 phút)",
                code="CONFIRM_REQUIRED",
                status_code=422,
            )
        await self.pos_settings.verify_advanced_password(current, body.advanced_password)
        output_dir = self.settings.data_dir / "validation_web" / datetime.now(UTC).strftime("%Y%m%d-%H%M%S")

        def done(report: dict[str, Any] | None, error: BaseException | None) -> None:
            finished = {**self._state, "finished_at": now_iso()}
            if error is not None:
                self._state = {**finished, "status": "error", "error": str(error)[:300]}
            else:
                assert report is not None
                self._state = {
                    **finished,
                    "status": "done",
                    "result": _gate_metrics(report),
                    "report_dir": str(output_dir),
                }

        self.worker.start_validation(None, output_dir, done)  # 409 SYSTEM_BUSY when one is running
        self._state = {"status": "running", "started_at": now_iso(), "by": current.username}
        return await self.status()

    # ---------------------------------------------------------------- evidence test

    async def evidence_test(self, data: bytes) -> EvidenceTestOut:
        if self.worker.validating:
            raise _busy()
        if not await asyncio.to_thread(is_image, data):
            raise AppError("Không giải mã được ảnh", code="IMAGE_DECODE_ERROR", status_code=400)
        path = self.settings.media_dir / "_test" / f"test_{uuid.uuid4().hex[:10]}.jpg"
        path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(path.write_bytes, data)
        thresholds = await self.pos_settings.public()
        try:
            result = await self.worker.recognize(
                str(path), thresholds.similarity_threshold, thresholds.min_confidence_accept
            )
        except TimeoutError as exc:
            raise AppError("Nhận diện quá lâu", code="PIPELINE_TIMEOUT", status_code=504) from exc
        finally:
            path.unlink(missing_ok=True)

        from engine.catalog.validation import normalize_ocr_token, rgb_to_hex

        evidence = await self.catalog.repo.evidence()
        keywords = {pid: [str(t) for t in (ev.get("ocr_keywords") or [])] for pid, ev in evidence.items()}
        colors = {c.code: (c.r, c.g, c.b) for c in await self.catalog.repo.colors()}
        barcodes = {p.barcode: p.id for p in await self.catalog.repo.active_products() if p.barcode}
        names = await self.catalog.lookup({d.product_id for d in result.detections})
        items = []
        for detection in result.detections:
            found = detection.evidence or {}
            ocr_text = str((found.get("ocr") or {}).get("text") or "")
            normalized = normalize_ocr_token(ocr_text)
            hits = sorted(
                {pid for pid, tokens in keywords.items() for token in tokens if token and token in normalized},
                key=lambda pid: (len(pid), pid),
            )
            rgb = (found.get("color") or {}).get("dominant_rgb")
            nearest = None
            if rgb and colors:
                code, distance = min(
                    (
                        (c, sum((float(a) - b) ** 2 for a, b in zip(rgb, v, strict=False)) ** 0.5)
                        for c, v in colors.items()
                    ),
                    key=lambda pair: pair[1],
                )
                nearest = NearestColorOut(code=code, rgb_distance=round(distance, 1))
            read = [
                str(b.get("data") if isinstance(b, dict) else b)
                for b in ((found.get("barcode") or {}).get("barcodes") or [])
            ]
            product = names.get(detection.product_id)
            items.append(
                TestedObjectOut(
                    product_id=detection.product_id,
                    name=product.name if product else f"SKU {detection.product_id}",
                    status=detection.status,
                    bbox=[round(float(v)) for v in detection.bbox],
                    ocr_text=ocr_text,
                    ocr_keyword_hits=hits,
                    color_hex=rgb_to_hex(*[round(float(v)) for v in rgb]) if rgb else None,
                    color_nearest=nearest,
                    barcodes=read,
                    barcode_skus=[barcodes[c] for c in read if c in barcodes],
                    plugins=sorted(found),
                )
            )
        return EvidenceTestOut(
            items=items, detected_count=result.detected_count, processing_time_ms=result.processing_time_ms
        )
