"""The real recognizer: the engine's pipeline, loaded once in this process.

Importing this module imports the engine and, through it, faiss and (for the real backends) torch.
Only `app.main.build_recognizer` imports it, and only when RECOGNIZER=local.
"""

import gc
import logging
import sys
import time
from pathlib import Path
from typing import Any

from app.modules.recognition.ports import Detection, Recognition
from engine.core.config import build_config
from engine.inference.infer import InferenceRunner

logger = logging.getLogger("app.recognition.local")


def _free_gpu() -> None:
    gc.collect()
    torch = sys.modules.get("torch")
    if torch is not None and torch.cuda.is_available():
        torch.cuda.empty_cache()


class LocalRecognizer:
    name = "local"

    def __init__(self, pipeline_config: Path, catalog_db_url: str, overrides: dict[str, Any] | None = None):
        if not pipeline_config.is_file():
            raise FileNotFoundError(f"PIPELINE_CONFIG không tồn tại: {pipeline_config}")
        self._config_path, self._catalog_db_url = pipeline_config, catalog_db_url
        # the admin's engine-setting overrides (config_overrides) over the YAML
        self._overrides = dict(overrides or {})
        # the engine loads its weights here, once; a failure stops the process at startup
        self._runner = self._build(self._overrides)

    def _build(self, overrides: dict[str, Any]) -> InferenceRunner:
        # the catalog is the web's database, whatever catalog source the YAML names for the CLI
        return InferenceRunner(build_config(self._config_path, overrides, catalog_db_url=self._catalog_db_url))

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

    def reload_catalog(self) -> None:
        self._runner.reload_catalog()

    def reload_pipeline(self, overrides: dict[str, Any]) -> None:
        """A 4 GB GPU cannot hold two pipelines: the old one is released before the new one loads.
        If the new one fails, the old settings are loaded back, then the error is raised."""
        previous = self._overrides
        del self._runner
        _free_gpu()
        try:
            self._runner = self._build(overrides)
            self._overrides = dict(overrides)
        except Exception:
            logger.exception("pipeline reload failed, loading the previous settings back")
            _free_gpu()
            self._runner = self._build(previous)
            raise

    def validate(self, benchmark_dir: str | None, output_dir: Path) -> dict[str, Any]:
        """The benchmark validation on the pipeline already loaded (no second copy on the GPU)."""
        from engine.validation.validate import ValidationRunner

        config = build_config(
            self._config_path,
            {**self._overrides, "paths.output_dir": str(output_dir)},
            catalog_db_url=self._catalog_db_url,
        )
        benchmark = benchmark_dir or str(config.resolve_path(config.paths.benchmark_dir))
        report: dict[str, Any] = ValidationRunner(config, pipeline=self._runner.pipeline).run(benchmark)
        return report

    def close(self) -> None:
        return None
