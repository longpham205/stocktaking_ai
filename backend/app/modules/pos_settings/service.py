"""The settings every logged-in account may read: stored values over the process defaults."""

from app.modules.pos_settings.config import PosSettings
from app.modules.pos_settings.repository import SettingsRepository
from app.modules.pos_settings.schemas import SettingsOut

PUBLIC_KEYS = tuple(SettingsOut.model_fields)


class SettingsService:
    def __init__(self, repo: SettingsRepository, defaults: PosSettings):
        self.repo, self.defaults = repo, defaults

    async def public(self) -> SettingsOut:
        stored = await self.repo.values(PUBLIC_KEYS)
        return SettingsOut(
            allow_checkout_without_price=bool(
                stored.get("allow_checkout_without_price", self.defaults.allow_checkout_without_price)
            ),
            similarity_threshold=stored.get("similarity_threshold"),
            min_confidence_accept=stored.get("min_confidence_accept"),
            tilt_block_capture=bool(stored.get("tilt_block_capture", False)),
            auto_print_receipt=bool(stored.get("auto_print_receipt", False)),
        )
