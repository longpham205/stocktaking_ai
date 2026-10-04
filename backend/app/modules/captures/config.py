"""Settings of the captures module (same `.env` as the core settings)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.config import BACKEND_DIR


class CapturesSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(BACKEND_DIR.parent / ".env", BACKEND_DIR / ".env"),
        extra="ignore",
    )

    # a bigger upload is refused with 413 IMAGE_TOO_LARGE (the phone downscales before sending)
    max_upload_mb: float = 10
    # the same Idempotency-Key on the same order within this window returns the first job
    idempotency_window_seconds: float = 5
    # captures waiting for recognition; one more is refused with 503 QUEUE_FULL
    recognition_queue_max: int = 20
    # a recognition running longer is reported as PIPELINE_TIMEOUT
    recognition_timeout_seconds: float = 60
    thumb_width: int = 240

    @property
    def max_upload_bytes(self) -> int:
        return int(self.max_upload_mb * 1024 * 1024)


@lru_cache
def get_captures_settings() -> CapturesSettings:
    return CapturesSettings()
