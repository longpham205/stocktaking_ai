"""Request and response bodies of the settings routes (field names are the legacy API's)."""

from typing import Any

from pydantic import BaseModel, ConfigDict, StrictBool, field_validator


class SettingsOut(BaseModel):
    allow_checkout_without_price: bool
    # null: the engine's own threshold applies
    similarity_threshold: float | None
    min_confidence_accept: float | None
    tilt_block_capture: bool
    auto_print_receipt: bool


# the thresholds an admin may set, and their bounds (outside them the recognition breaks down)
THRESHOLD_BOUNDS = {"similarity_threshold": (0.3, 0.95), "min_confidence_accept": (0.3, 0.99)}
FLAGS = ("allow_checkout_without_price", "tilt_block_capture", "auto_print_receipt")


class SettingsPatch(BaseModel):
    """Only the fields sent are changed; unknown fields are ignored (legacy). A threshold set to
    null goes back to the engine's own value."""

    model_config = ConfigDict(extra="ignore")

    allow_checkout_without_price: StrictBool | None = None
    tilt_block_capture: StrictBool | None = None
    auto_print_receipt: StrictBool | None = None
    similarity_threshold: float | None = None
    min_confidence_accept: float | None = None

    @field_validator("similarity_threshold", "min_confidence_accept", mode="before")
    @classmethod
    def _a_number(cls, value: Any) -> Any:
        if isinstance(value, bool) or not (value is None or isinstance(value, int | float)):
            raise ValueError("phải là số")
        return value
