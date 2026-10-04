"""Request and response bodies of the catalog routes (field names are the legacy API's)."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictInt

# VND, no minor unit: the largest price (or typed-in price) a line may have
MAX_PRICE = 100_000_000
# the evidence an admin edits, in the order the screen shows it
EVIDENCE_FIELDS = ("ocr_keywords", "color_code", "force_evidence", "confusable_with")
# what the admin ticks to say they know an evidence edit changes the recognition
CONFIRM_TEXT = "Tôi hiểu thay đổi này ảnh hưởng độ chính xác AI"


class ProductOut(BaseModel):
    id: str
    name: str
    barcode: str
    price: int | None
    needs_naming: bool
    missing_color_reference: bool


class ProductsOut(BaseModel):
    items: list[ProductOut]


class AdminProductsOut(BaseModel):
    total: int
    page: int
    size: int
    items: list[ProductOut]
    # over the whole catalog on sale, whatever the search and filter
    missing_price: int
    missing_barcode: int
    needs_naming: int


class ProductPatch(BaseModel):
    """Only the fields sent are changed. `price: null` removes the price, `barcode: ""` the barcode."""

    model_config = ConfigDict(extra="forbid")

    price: StrictInt | None = Field(None, ge=0, le=MAX_PRICE)
    barcode: str | None = None
    name: str | None = None


class EvidencePatch(BaseModel):
    """Only the fields sent are changed; an empty value removes that evidence. Values are checked
    and normalised by the service (as the engine reads them), so they arrive as sent."""

    model_config = ConfigDict(extra="ignore")

    confirm: Any = None
    ocr_keywords: Any = None
    color_code: Any = None
    force_evidence: Any = None
    confusable_with: Any = None


class ColorOut(BaseModel):
    code: str
    # null: a product names this colour but it has no reference value yet (`missing`)
    hex: str | None
    r: int | None = None
    g: int | None = None
    b: int | None = None
    source: str | None = None
    used_by: list[str]
    missing: bool


class ColorsOut(BaseModel):
    items: list[ColorOut]


class ColorPatch(BaseModel):
    """`hex`: `#RRGGBB` (the `#` optional), or null to remove the reference."""

    model_config = ConfigDict(extra="ignore")

    confirm: Any = None
    hex: str | None = None


class EvidenceOut(BaseModel):
    product: ProductOut
    evidence: dict[str, Any]
    colors: list[ColorOut]
    confirm_text: str
    ocr_min_length: int
    # signed URLs of the product's gallery photos, scaled down
    gallery: list[str]
    warnings: list[str] = []
