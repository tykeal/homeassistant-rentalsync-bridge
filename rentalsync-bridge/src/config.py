# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Application configuration using Pydantic settings."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Database
    database_url: str = Field(
        default="sqlite:///./data/rentalsync.db",
        description="SQLite database URL",
    )

    # Cloudbeds API
    cloudbeds_client_id: str = Field(
        default="",
        description="Cloudbeds OAuth client ID",
    )
    cloudbeds_client_secret: str = Field(
        default="",
        description="Cloudbeds OAuth client secret",
    )

    # Sync configuration
    sync_interval_minutes: int = Field(
        default=5,
        ge=1,
        le=60,
        description="Background sync interval in minutes",
    )

    # Security
    encryption_key: str = Field(
        default="",
        description="Fernet encryption key for OAuth tokens",
    )

    # Application mode
    standalone_mode: bool = Field(
        default=False,
        description="Disable Home Assistant auth for development",
    )

    # iCal base URL for HA add-on mode (internal container hostname)
    # Format: http://hostname:port (e.g., http://local-rentalsync-bridge:8099)
    ical_base_url: str = Field(
        default="",
        description="Base URL for iCal endpoints in HA add-on mode",
    )

    # Server configuration
    host: str = Field(
        default="0.0.0.0",
        description="Server host",
    )
    port: int = Field(
        default=8099,
        description="Server port",
    )
    log_level: str = Field(
        default="INFO",
        description="Logging level",
    )


@lru_cache
def get_settings() -> Settings:
    """Get cached application settings.

    Returns:
        Application settings instance.
    """
    return Settings()
