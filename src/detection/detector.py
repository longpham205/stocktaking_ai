"""Product localization (object detection) module.

Responsibility: locate product bounding boxes within a query image. This
module must NEVER perform classification, retrieval, cropping, plugin
execution, or file persistence (see 02_MODULE_SPECIFICATION.md, Section 3.1).

`Detector` itself is a thin dispatcher: it selects and loads exactly one
concrete `DetectionBackend` implementation (see `src/detection/backends/`)
based on `configs/config.yaml -> detection.backend`, then delegates the
actual detection work to it. This keeps the public API,
`Detector.detect(image_data) -> DetectionResult`, completely stable while
new models can be added at any time:

    1. Create `src/detection/backends/<name>.py` implementing
       `DetectionBackend.detect(image_array) -> list[Detection]`.
    2. Register it with one new branch in `Detector._load_backend` below.
    3. Point `configs/config.yaml -> detection.backend` at `<name>`.

No other module (Cropper, InventoryPipeline, ...) ever needs to change
(03_DEVELOPMENT_RULES.md, Rule 25 - Extensibility Standard).

Backends currently available:
    - "mock_contour" (default): classical CV (Canny + contours), no
      downloaded weights required (`backends/mock_contour.py`).
    - "rf_detr": real neural detector via RF-DETR
      (`backends/rf_detr.py`); requires
      `pip install rfdetr torch torchvision supervision`.
"""

from __future__ import annotations

from src.core.config import AppConfig
from src.core.logger import get_logger
from src.core.utils import timer
from src.detection.backends.base import DetectionBackend
from src.models.models import DetectionResult, ImageData

logger = get_logger(__name__)


class Detector:
    """Locates product regions within an image and returns bounding boxes."""

    def __init__(self, config: AppConfig) -> None:
        """Initializes the Detector and loads its backend model exactly once.

        Args:
            config: Fully validated application configuration.
        """
        self._config = config.detection
        self._backend = self._load_backend()
        logger.info(
            "Detector initialized with backend='%s'",
            self._config.backend,
        )

    def _load_backend(self) -> DetectionBackend:
        """Instantiates the configured detection backend exactly once.

        Backend-specific (potentially heavy, e.g. `torch`/`rfdetr`)
        dependencies are imported lazily inside each backend module, so
        selecting "mock_contour" never requires them to be installed.

        Returns:
            An initialized `DetectionBackend` instance.

        Raises:
            ValueError: If an unsupported backend name is configured.
            ImportError: If the configured backend's optional dependencies
                are not installed.
        """
        backend_name = self._config.backend

        if backend_name == "mock_contour":
            from src.detection.backends.mock_contour import MockContourBackend

            return MockContourBackend(self._config)

        if backend_name == "rf_detr":
            from src.detection.backends.rf_detr import RfDetrBackend

            return RfDetrBackend(self._config)

        raise ValueError(
            f"Unsupported detection backend: '{backend_name}'. "
            "Available backends: 'mock_contour', 'rf_detr'. To add a new "
            "one, implement src/detection/backends/<name>.py and register "
            "it in Detector._load_backend."
        )

    def detect(self, image_data: ImageData) -> DetectionResult:
        """Detects product bounding boxes within the given image.

        Args:
            image_data: The source image to run detection on.

        Returns:
            A DetectionResult containing all detections found, ordered by
            descending confidence and capped at `max_detections`.
        """
        with timer() as elapsed:
            detections = self._backend.detect(image_data.image_array)

        detections.sort(key=lambda item: item.confidence, reverse=True)
        detections = detections[: self._config.max_detections]

        logger.info(
            "Detector found %d region(s) in image_id='%s' (%.2f ms)",
            len(detections),
            image_data.image_id,
            elapsed["elapsed_ms"],
        )

        return DetectionResult(
            image_id=image_data.image_id,
            detections=detections,
            processing_time_ms=elapsed["elapsed_ms"],
        )
