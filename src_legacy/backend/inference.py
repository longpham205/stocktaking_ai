"""Cầu nối tới pipeline AI. Backend gọi pipeline CHỈ qua `InferenceRunner` (không import model/plugin cụ thể)."""

from __future__ import annotations

import hashlib
import inspect
import logging
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol

ROOT = Path(__file__).resolve().parents[1]
log = logging.getLogger("backend.inference")


@dataclass
class Detection:
    product_id: str
    bbox: tuple[float, float, float, float]  # x1, y1, x2, y2 (pixel, ảnh gốc)
    status: str  # accepted | uncertain
    plugin_evidence: dict = field(default_factory=dict)


@dataclass
class InferenceOutput:
    detections: list[Detection]
    processing_time_ms: float
    has_overlap: bool = False  # pipeline báo ảnh có chồng lấp đáng kể (gợi ý chụp thêm)
    detected_count: int | None = None  # số vật detector tìm thấy (None = pipeline chưa báo cáo)
    rejected_bboxes: list = field(default_factory=list)  # (x1,y1,x2,y2) vật phát hiện nhưng không nhận ra (khung đỏ)


class Executor(Protocol):
    def infer(self, image_path: str, similarity_threshold: float | None = None,
              min_confidence_accept: float | None = None) -> InferenceOutput: ...


class LocalExecutor:
    """Chạy pipeline trong cùng tiến trình qua `InferenceRunner` (nạp model một lần lúc khởi động)."""

    def __init__(self, pipeline_config: Path, overrides: dict | None = None) -> None:
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        self._pipeline_config = Path(pipeline_config)
        self._overrides = dict(overrides or {})
        self._runner = self._build_runner(self._overrides)
        # Tương thích cả trước và sau bản vá P1-2/P1-4: chỉ truyền tham số mà pipeline hỗ trợ.
        self._params = set(inspect.signature(self._runner.run_single).parameters)
        self._warned_min_conf = False

    def _build_runner(self, overrides: dict):
        from src.core.config import build_config  # import chậm: cần môi trường AI đầy đủ
        from src.inference.infer import InferenceRunner

        return InferenceRunner(build_config(self._pipeline_config, overrides))

    @staticmethod
    def _free_gpu() -> None:
        import gc

        gc.collect()
        torch = sys.modules.get("torch")
        if torch is not None and torch.cuda.is_available():
            torch.cuda.empty_cache()

    def validate(self, benchmark_dir: str | None, output_dir: Path) -> dict:
        """Kiểm định trên benchmark, DÙNG LẠI pipeline đang nạp (không nạp bản thứ hai lên GPU).
        Gọi trên luồng suy luận. Báo cáo ghi vào ``output_dir`` (không đè outputs thường)."""
        from src.core.config import build_config
        from src.validation.validate import ValidationRunner

        cfg = build_config(self._pipeline_config, {**self._overrides, "paths.output_dir": str(output_dir)})
        bench = benchmark_dir or str(cfg.resolve_path(cfg.paths.benchmark_dir))
        return ValidationRunner(cfg, pipeline=self._runner.pipeline).run(bench)

    def reload_pipeline(self, overrides: dict) -> None:
        """Nạp lại pipeline với thiết lập nâng cao mới. GPU 4GB không giữ được hai pipeline: gỡ cái cũ trước.
        Nạp lỗi -> nạp lại thiết lập cũ (rollback) rồi báo lỗi. PHẢI gọi trên luồng suy luận (tuần tự)."""
        old = self._overrides
        self._runner = None
        self._free_gpu()
        try:
            self._runner = self._build_runner(overrides)
            self._overrides = dict(overrides)
        except Exception:
            log.exception("Nạp pipeline với thiết lập mới thất bại; quay về thiết lập cũ")
            self._free_gpu()
            self._runner = self._build_runner(old)
            raise

    def reload_catalog(self) -> None:
        """Nạp lại catalog của pipeline sau khi admin sửa (barcode, bằng chứng, màu tham chiếu)."""
        fn = getattr(self._runner, "reload_catalog", None)
        if fn is not None:
            fn()

    def infer(self, image_path: str, similarity_threshold: float | None = None,
              min_confidence_accept: float | None = None) -> InferenceOutput:
        t0 = time.perf_counter()
        kwargs: dict = {"similarity_threshold": similarity_threshold}
        if "persist" in self._params:
            kwargs["persist"] = False  # web tự lưu bản ghi; không ghi đè file kết quả dùng chung mỗi lần chụp
        if min_confidence_accept is not None:
            if "min_confidence_accept" in self._params:
                kwargs["min_confidence_accept"] = min_confidence_accept
            elif not self._warned_min_conf:
                self._warned_min_conf = True
                log.warning("Pipeline chưa hỗ trợ min_confidence_accept (chưa áp dụng bản vá P1-4): bỏ qua thiết lập này.")
        result = self._runner.run_single(image_path, **kwargs)
        dets = [
            Detection(
                product_id=str(it.product_id),
                bbox=(it.bbox.x1, it.bbox.y1, it.bbox.x2, it.bbox.y2),
                status=it.status,
                plugin_evidence=dict(it.plugin_evidence or {}),
            )
            for it in result.items
            if it.status in ("accepted", "uncertain")
        ]
        ms = float(getattr(result, "processing_time_ms", 0.0)) or (time.perf_counter() - t0) * 1000
        detected = getattr(result, "detected_count", None)
        rejected = [(b.x1, b.y1, b.x2, b.y2) for b in (getattr(result, "rejected_bboxes", None) or [])]
        return InferenceOutput(dets, ms, bool(getattr(result, "has_overlap", False)),
                               int(detected) if isinstance(detected, (int, float)) else None, rejected)


class FakeExecutor:
    """Bộ suy luận GIẢ (không cần model): để thử giao diện/API và kiểm thử. Xác định theo nội dung ảnh."""

    def __init__(self, product_ids: list[str], delay: float = 0.4,
                 fn: Callable[[str], InferenceOutput] | None = None) -> None:
        self._ids, self._delay, self._fn = product_ids, delay, fn
        self.overrides: dict = {}
        self.fail_reload = False  # test: giả lập nạp pipeline lỗi

    def validate(self, benchmark_dir: str | None, output_dir: Path) -> dict:
        time.sleep(self._delay)
        return {"end_to_end": {"f1": 0.5, "precision": 0.5, "recall": 0.5},
                "fusion": {"accuracy_after": 0.6}, "fake": True}

    def reload_pipeline(self, overrides: dict) -> None:
        if self.fail_reload:
            raise RuntimeError("giả lập: không nạp được pipeline")
        self.overrides = dict(overrides)

    def infer(self, image_path: str, similarity_threshold: float | None = None,
              min_confidence_accept: float | None = None) -> InferenceOutput:
        if self._fn:
            return self._fn(image_path)
        time.sleep(self._delay)
        import cv2  # chỉ để lấy kích thước ảnh

        img = cv2.imread(image_path)
        if img is None:
            raise ValueError("Không đọc được ảnh")
        h, w = img.shape[:2]
        seed = int(hashlib.sha256(Path(image_path).read_bytes()).hexdigest()[:8], 16)
        n = 3 + seed % 4
        dets = []
        for i in range(n):
            pid = self._ids[(seed + i * 7) % len(self._ids)]
            col, row = i % 3, i // 3
            x1, y1 = int(w * (0.06 + col * 0.31)), int(h * (0.08 + row * 0.44))
            dets.append(Detection(pid, (x1, y1, x1 + int(w * 0.26), y1 + int(h * 0.38)),
                                  "uncertain" if i == n - 1 else "accepted"))
        if n >= 4:  # thêm một dòng trùng SKU để thử luật gộp
            dets.append(Detection(dets[0].product_id, (5, 5, int(w * 0.2), int(h * 0.2)), "accepted"))
        extra = 2 if seed % 3 == 0 else 0  # giả lập vài vật detector thấy nhưng không nhận ra
        rejected = [(int(w * (0.72 + 0.12 * k)), int(h * 0.82), int(w * (0.82 + 0.12 * k)), int(h * 0.97)) for k in range(extra)]
        return InferenceOutput(dets, 350.0 + seed % 200, has_overlap=n >= 5, detected_count=len(dets) + extra,
                               rejected_bboxes=rejected)
