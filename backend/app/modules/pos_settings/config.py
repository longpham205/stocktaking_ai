"""Settings of the POS module (same `.env` as the core settings): the defaults a shop starts with,
until an admin stores a value in the `settings` table."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.config import BACKEND_DIR


class PosSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(BACKEND_DIR.parent / ".env", BACKEND_DIR / ".env"),
        extra="ignore",
    )

    # false: an order with a line that has no price cannot be paid (409 PRICE_MISSING_BLOCKED)
    allow_checkout_without_price: bool = False


@lru_cache
def get_pos_settings() -> PosSettings:
    return PosSettings()
