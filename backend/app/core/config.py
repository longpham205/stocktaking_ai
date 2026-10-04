"""Process settings shared by every module, read from the environment or a `.env`
(the repo root's, or backend/'s). A module that needs its own settings adds an `XSettings` next to
its code; nothing module-specific belongs here."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]


class CoreSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(BACKEND_DIR.parent / ".env", BACKEND_DIR / ".env"),
        extra="ignore",
    )

    app_env: Literal["dev", "test", "prod"] = "dev"

    # Postgres through asyncpg. The default is `make docker-up-data` (compose's postgres on :5437).
    # 127.0.0.1, not localhost: on Windows with Docker Desktop, localhost tries IPv6 first and each
    # new connection stalls ~20 s before falling back.
    database_url: str = "postgresql+asyncpg://stocktaking:stocktaking@127.0.0.1:5437/stocktaking"
    db_pool_size: int = 5
    db_max_overflow: int = 5

    # Runtime assets (gallery, FAISS cache, capture images). Relative paths resolve from backend/.
    data_dir: Path = Path("data")
    # The engine's pipeline config (configs/config.yaml, or config.demo.yaml for the mock backends).
    pipeline_config: Path = Path("configs/config.yaml")

    # Which recognizer the API process builds: `local` loads the engine (torch, GPU);
    # `fake` returns canned results and must never import the engine's heavy dependencies.
    recognizer: Literal["local", "fake"] = "local"

    # Signs capture/gallery image URLs. No default: a process without it cannot sign media URLs.
    media_url_secret: str = ""
    media_url_ttl_seconds: int = 3600
    # Capture photos and their thumbnails. Relative paths resolve from backend/.
    media_dir: Path = Path("data/transactions")

    # "Today" in reports and history is the shop's day, not UTC's.
    timezone_offset_hours: float = 7.0

    log_level: str = "INFO"
    log_format: Literal["console", "json"] = "console"

    @field_validator("database_url")
    @classmethod
    def _async_postgres(cls, value: str) -> str:
        if not value.startswith("postgresql+asyncpg://"):
            raise ValueError("DATABASE_URL must be a postgresql+asyncpg:// URL")
        return value

    @field_validator("data_dir", "pipeline_config", "media_dir")
    @classmethod
    def _from_backend_dir(cls, value: Path) -> Path:
        return value if value.is_absolute() else BACKEND_DIR / value


@lru_cache
def get_settings() -> CoreSettings:
    return CoreSettings()
