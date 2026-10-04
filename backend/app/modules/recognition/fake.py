"""A recognizer that loads no model: for development on a machine without a GPU and for the test
suite. It must stay importable without torch, faiss, OpenCV or the engine's pipeline modules
(tests/test_import_boundary.py).

The result depends only on the photo's bytes: the same photo gives the same basket, so a demo is
repeatable. The products are picked from the catalog that is on sale when the photo is processed.
"""

import hashlib
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, text

from app.modules.recognition.mapper import image_size
from app.modules.recognition.ports import Box, Detection, Recognition


class FakeRecognizer:
    name = "fake"

    def __init__(self, database_url: str | None = None, product_ids: Sequence[str] = (), delay_seconds: float = 0.4):
        """`database_url` (synchronous driver): pick from the products on sale there, read at each
        photo; otherwise from `product_ids`."""
        self._database_url, self._product_ids, self._delay = database_url, list(product_ids), delay_seconds
        self._engine: Engine | None = None
        # the engine-setting overrides it was last asked to apply (it applies none)
        self.overrides: dict[str, Any] = {}

    def _ids(self) -> list[str]:
        if self._database_url is None:
            return self._product_ids
        if self._engine is None:
            self._engine = create_engine(self._database_url, pool_size=1, max_overflow=0, pool_pre_ping=True)
        query = text("SELECT product_id FROM product WHERE is_active ORDER BY length(product_id), product_id")
        with self._engine.connect() as conn:
            return [str(pid) for pid in conn.execute(query).scalars()]

    def recognize(
        self, image_path: str, similarity_threshold: float | None = None, min_confidence_accept: float | None = None
    ) -> Recognition:
        time.sleep(self._delay)
        size = image_size(Path(image_path))
        if size is None:
            raise ValueError("Không đọc được ảnh")
        width, height = size
        ids = self._ids()
        seed = int(hashlib.sha256(Path(image_path).read_bytes()).hexdigest()[:8], 16)
        if not ids:
            return Recognition([], 350.0 + seed % 200, detected_count=0)
        count = 3 + seed % 4
        detections = []
        for i in range(count):
            column, row = i % 3, i // 3
            x1, y1 = int(width * (0.06 + column * 0.31)), int(height * (0.08 + row * 0.44))
            box: Box = (x1, y1, x1 + int(width * 0.26), y1 + int(height * 0.38))
            status = "uncertain" if i == count - 1 else "accepted"
            detections.append(Detection(ids[(seed + i * 7) % len(ids)], box, status))
        if count >= 4:  # one more of the first product, for the merge rule
            extra: Box = (5, 5, int(width * 0.2), int(height * 0.2))
            detections.append(Detection(detections[0].product_id, extra, "accepted"))
        unrecognised = 2 if seed % 3 == 0 else 0
        rejected: list[Box] = [
            (int(width * (0.72 + 0.12 * k)), int(height * 0.82), int(width * (0.82 + 0.12 * k)), int(height * 0.97))
            for k in range(unrecognised)
        ]
        return Recognition(
            detections,
            350.0 + seed % 200,
            has_overlap=count >= 5,
            detected_count=len(detections) + unrecognised,
            rejected_bboxes=rejected,
        )

    def reload_catalog(self) -> None:
        return None  # reads the catalog at every photo

    def reload_pipeline(self, overrides: dict[str, Any]) -> None:
        self.overrides = dict(overrides)

    def validate(self, benchmark_dir: str | None, output_dir: Path) -> dict[str, Any]:
        time.sleep(self._delay)
        return {
            "end_to_end": {"f1": 0.5, "precision": 0.5, "recall": 0.5},
            "fusion": {"accuracy_after": 0.6},
            "fake": True,
        }

    def close(self) -> None:
        if self._engine is not None:
            self._engine.dispose()
