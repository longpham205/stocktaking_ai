"""The plug the web app sees the ML engine through. The app depends on this protocol only; which
adapter implements it (`local_pipeline`: the engine, `fake`: canned results) is decided once, in
`app.main.build_recognizer`. Reload, validation and evidence tests arrive with phase 4."""

from dataclasses import dataclass, field
from typing import Any, Protocol

# x1, y1, x2, y2 in pixels of the source image
Box = tuple[float, float, float, float]


@dataclass(frozen=True)
class Detection:
    product_id: str
    bbox: Box
    # accepted | uncertain (rejected objects are only boxes: `Recognition.rejected_bboxes`)
    status: str
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Recognition:
    detections: list[Detection]
    processing_time_ms: float
    # the overlap stage thinks objects hide each other: suggest another photo
    has_overlap: bool = False
    # objects the detector found; None when the recognizer does not report it
    detected_count: int | None = None
    # objects found but not recognised (drawn red, they become no order line)
    rejected_bboxes: list[Box] = field(default_factory=list)


class RecognizerPort(Protocol):
    name: str

    def recognize(
        self, image_path: str, similarity_threshold: float | None = None, min_confidence_accept: float | None = None
    ) -> Recognition:
        """Blocking (a GPU call): the worker runs it on its single thread."""
        ...

    def reload_catalog(self) -> None:
        """Re-read the catalog after an admin edit (barcode, evidence, colours). On the worker thread."""
        ...

    def close(self) -> None: ...
