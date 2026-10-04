"""Response bodies of the catalog routes (field names are the legacy API's)."""

from pydantic import BaseModel


class ProductOut(BaseModel):
    id: str
    name: str
    barcode: str
    price: int | None
    needs_naming: bool
    missing_color_reference: bool


class ProductsOut(BaseModel):
    items: list[ProductOut]
