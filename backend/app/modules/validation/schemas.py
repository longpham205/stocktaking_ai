"""Request and response bodies of the validation and evidence-test routes (the legacy API's names)."""

from typing import Any

from pydantic import BaseModel


class ValidationIn(BaseModel):
    confirm: Any = None
    advanced_password: str | None = None


class ValidationOut(BaseModel):
    # idle | running | done | error
    status: str
    started_at: str | None = None
    finished_at: str | None = None
    # the admin who started it
    by: str | None = None
    # {"f1", "fusion_accuracy"} of this run, and of the stored baseline
    result: dict[str, Any] | None = None
    baseline: dict[str, Any] | None = None
    report_dir: str | None = None
    error: str | None = None


class NearestColorOut(BaseModel):
    code: str
    rgb_distance: float


class TestedObjectOut(BaseModel):
    product_id: str
    name: str
    # accepted | uncertain
    status: str
    bbox: list[int]
    # what OCR read, and the products whose catalog keywords appear in it
    ocr_text: str
    ocr_keyword_hits: list[str]
    # the measured colour, and the closest colour reference
    color_hex: str | None
    color_nearest: NearestColorOut | None
    # barcodes read, and the products that carry them
    barcodes: list[str]
    barcode_skus: list[str]
    # plugins that reported evidence
    plugins: list[str]


class EvidenceTestOut(BaseModel):
    items: list[TestedObjectOut]
    detected_count: int | None
    processing_time_ms: float
