"""Response bodies of the settings routes (field names are the legacy API's)."""

from pydantic import BaseModel


class SettingsOut(BaseModel):
    allow_checkout_without_price: bool
    # null: the engine's own threshold applies
    similarity_threshold: float | None
    min_confidence_accept: float | None
    tilt_block_capture: bool
    auto_print_receipt: bool
