"""Application configuration, loaded from environment variables (and an optional .env file).

Nothing secret has a default: the app refuses to start without DATABASE_URL and JWT_SECRET_KEY.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Looked up relative to the working directory: `backend/.env` or the repo-root `.env`.
_ENV_FILES = (".env", "../.env")


class DatabaseSettings(BaseSettings):
    """Minimal settings used by Alembic, so migrations don't require the full app config."""

    model_config = SettingsConfigDict(env_file=_ENV_FILES, extra="ignore")

    database_url: str


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_ENV_FILES, extra="ignore", case_sensitive=False)

    # --- General ---------------------------------------------------------------------------
    app_name: str = "DocuMind"
    environment: Literal["development", "test", "production"] = "development"
    api_v1_prefix: str = "/api/v1"
    log_level: str = "INFO"
    log_json: bool = True
    cors_origins: str = "http://localhost:3000"

    # --- Datastores ------------------------------------------------------------------------
    database_url: str
    db_pool_size: int = 10
    db_max_overflow: int = 10
    db_echo: bool = False
    redis_url: str = "redis://localhost:6379/0"
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: SecretStr | None = None
    qdrant_collection_alias: str = "chunks_active"

    # --- Auth ------------------------------------------------------------------------------
    jwt_secret_key: SecretStr
    jwt_algorithm: Literal["HS256", "HS384", "HS512"] = "HS256"
    jwt_issuer: str = "documind"
    jwt_audience: str = "documind-api"
    access_token_ttl_minutes: int = Field(default=15, ge=1, le=120)
    refresh_token_ttl_days: int = Field(default=7, ge=1, le=90)
    # A rotated refresh token re-presented within this window is treated as a benign race
    # (e.g. two tabs refreshing at once) rather than token theft.
    refresh_reuse_grace_seconds: int = Field(default=10, ge=0, le=60)
    refresh_cookie_name: str = "documind_refresh"
    cookie_secure: bool = True
    cookie_domain: str | None = None
    login_max_failed_attempts: int = Field(default=5, ge=1)
    login_lockout_minutes: int = Field(default=15, ge=1)
    password_min_length: int = Field(default=10, ge=8)

    # --- Uploads & storage -----------------------------------------------------------------
    storage_backend: Literal["local"] = "local"
    storage_local_path: Path = Path("./storage")
    max_upload_size_mb: int = Field(default=50, ge=1)
    max_files_per_upload: int = Field(default=20, ge=1, le=100)
    max_uncompressed_ooxml_mb: int = Field(default=500, ge=1)
    default_document_visibility: Literal["private", "organization", "restricted"] = "organization"

    # --- Rate limiting (per minute) --------------------------------------------------------
    rate_limit_enabled: bool = True
    rate_limit_auth_per_minute: int = 10
    rate_limit_upload_per_minute: int = 20
    rate_limit_default_per_minute: int = 300

    @field_validator("jwt_secret_key")
    @classmethod
    def _secret_long_enough(cls, v: SecretStr) -> SecretStr:
        if len(v.get_secret_value()) < 32:
            raise ValueError("JWT_SECRET_KEY must be at least 32 characters")
        return v

    @model_validator(mode="after")
    def _production_guards(self) -> Settings:
        if self.environment == "production":
            if not self.cookie_secure:
                raise ValueError("COOKIE_SECURE must be true in production")
            if "*" in self.cors_origin_list:
                raise ValueError("CORS_ORIGINS must not contain '*' in production")
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def max_upload_size_bytes(self) -> int:
        return self.max_upload_size_mb * 1024 * 1024

    @property
    def max_uncompressed_ooxml_bytes(self) -> int:
        return self.max_uncompressed_ooxml_mb * 1024 * 1024

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # values come from the environment
