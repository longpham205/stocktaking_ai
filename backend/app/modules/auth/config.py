"""Settings of the auth module (same `.env` as the core settings)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.config import BACKEND_DIR


class AuthSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(BACKEND_DIR.parent / ".env", BACKEND_DIR / ".env"),
        extra="ignore",
    )

    # Signs the login tokens. No default: without it nobody can log in (`make setup` generates it).
    jwt_secret: str = ""
    # one working day
    token_ttl_minutes: int = 480
    # this many wrong passwords for one (account, IP) locks that pair for the lockout period
    login_max_failed_attempts: int = 5
    login_lockout_minutes: int = 5


@lru_cache
def get_auth_settings() -> AuthSettings:
    return AuthSettings()
