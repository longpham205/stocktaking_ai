"""The real recognizer: the engine's pipeline, loaded once in this process.

Importing this module imports the engine and, through it, faiss and (for the real backends) torch.
Only `app.main.build_recognizer` imports it, and only when RECOGNIZER=local.
"""

import time
from pathlib import Path

from app.modules.recognition.ports import Detection, Recognition
from engine.core.config import build_config
from engine.inference.infer import InferenceRunner


class LocalRecognizer:
    name = "local"

    def __init__(self, pipeline_config: Path, catalog_db_url: str):
        if not pipeline_config.is_file():
            raise FileNotFoundError(f"PIPELINE_CONFIG không tồn tại: {pipeline_config}")
        # the engine loads its weights here, once; a failure stops the process at startup
        # the catalog is the web's database, whatever catalog source the YAML names for the CLI
        self._runner = InferenceRunner(build_config(pipeline_config, catalog_db_url=catalog_db_url))

    def recognize(
        self, image_path: str, similarity_threshold: float | None = None, min_confidence_accept: float | None = None
    ) -> Recognition:
        started = time.perf_counter()
        # persist=False: the web keeps its own records; the engine's shared output files stay untouched
        result = self._runner.run_single(
            image_path,
            similarity_threshold=similarity_threshold,
            min_confidence_accept=min_confidence_accept,
            persist=False,
        )
        detections = [
            Detection(
                product_id=str(item.product_id),
                bbox=(item.bbox.x1, item.bbox.y1, item.bbox.x2, item.bbox.y2),
                status=item.status,
                evidence=dict(item.plugin_evidence or {}),
            )
            for item in result.items
            if item.status in ("accepted", "uncertain")
        ]
        elapsed_ms = float(result.processing_time_ms or 0.0) or (time.perf_counter() - started) * 1000
        return Recognition(
            detections,
            elapsed_ms,
            has_overlap=bool(result.has_overlap),
            detected_count=int(result.detected_count),
            rejected_bboxes=[(b.x1, b.y1, b.x2, b.y2) for b in result.rejected_bboxes],
        )

    def close(self) -> None:
        return None
