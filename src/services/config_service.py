# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Configuration service for environment and runtime settings management."""

import logging
import re
from functools import lru_cache

from src.config import Settings, get_settings

logger = logging.getLogger(__name__)

# Valid IANA timezone pattern (simplified)
TIMEZONE_PATTERN = re.compile(r"^[A-Za-z_]+/[A-Za-z_]+(?:/[A-Za-z_]+)?$|^UTC$")


class ConfigService:
    """Service for managing application configuration.

    Provides validation and access to configuration settings with
    appropriate defaults and error handling.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        """Initialize ConfigService.

        Args:
            settings: Application settings. Defaults to global settings.
        """
        self._settings = settings or get_settings()

    @property
    def database_url(self) -> str:
        """Get database URL."""
        return self._settings.database_url

    @property
    def sync_interval_minutes(self) -> int:
        """Get sync interval in minutes."""
        return self._settings.sync_interval_minutes

    @property
    def is_standalone_mode(self) -> bool:
        """Check if running in standalone mode (no HA auth)."""
        return self._settings.standalone_mode

    @property
    def has_cloudbeds_credentials(self) -> bool:
        """Check if Cloudbeds credentials are configured."""
        return bool(
            self._settings.cloudbeds_client_id
            and self._settings.cloudbeds_client_secret
        )

    @property
    def has_encryption_key(self) -> bool:
        """Check if encryption key is configured."""
        return bool(self._settings.encryption_key)

    @staticmethod
    def validate_timezone(timezone: str) -> bool:
        """Validate an IANA timezone identifier.

        Args:
            timezone: Timezone string to validate (e.g., 'America/New_York').

        Returns:
            True if timezone appears to be valid IANA format.
        """
        if not timezone:
            return False
        return bool(TIMEZONE_PATTERN.match(timezone))

    @staticmethod
    def generate_slug(name: str) -> str:
        """Generate URL-safe slug from a name.

        Args:
            name: Name to convert to slug.

        Returns:
            URL-safe lowercase slug with hyphens.
        """
        # Convert to lowercase and replace spaces/underscores with hyphens
        slug = name.lower().strip()
        slug = re.sub(r"[\s_]+", "-", slug)
        # Remove non-alphanumeric characters except hyphens
        slug = re.sub(r"[^a-z0-9-]", "", slug)
        # Remove multiple consecutive hyphens
        slug = re.sub(r"-+", "-", slug)
        # Remove leading/trailing hyphens
        slug = slug.strip("-")
        return slug or "listing"

    def get_log_level(self) -> int:
        """Get logging level as integer.

        Returns:
            Logging level constant (e.g., logging.INFO).
        """
        level_map = {
            "DEBUG": logging.DEBUG,
            "INFO": logging.INFO,
            "WARNING": logging.WARNING,
            "ERROR": logging.ERROR,
            "CRITICAL": logging.CRITICAL,
        }
        return level_map.get(self._settings.log_level.upper(), logging.INFO)


@lru_cache
def get_config_service() -> ConfigService:
    """Get cached ConfigService instance.

    Returns:
        ConfigService singleton.
    """
    return ConfigService()
