"""What the rest of the app knows about a product: a plain record, no SQLAlchemy rows."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Product:
    id: str
    name: str
    # "" when the product has none
    barcode: str
    # None: nobody has priced it yet
    price: int | None
    # created from a new gallery folder, still waiting for an admin to name it
    needs_naming: bool
    # its colour-code evidence names a colour that has no reference value
    missing_color_reference: bool
