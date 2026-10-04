"""Response bodies of the catalog routes (field names are the legacy API's)."""

from pydantic import BaseModel, ConfigDict, Field, StrictInt

# VND, no minor unit: the largest price (or typed-in price) a line may have
MAX_PRICE = 100_000_000


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
    """Only the fields sent are changed. `price: null` removes the price."""

    # barcode and name arrive with the catalog writes (3e-2); until then they are refused
    model_config = ConfigDict(extra="forbid")

    price: StrictInt | None = Field(None, ge=0, le=MAX_PRICE)
