# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Cloudbeds API service wrapper using cloudbeds-pms SDK."""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from src.config import get_settings

logger = logging.getLogger(__name__)


PHONE_LAST_DIGITS = 4


class CloudbedsServiceError(Exception):
    """Exception raised for Cloudbeds API errors."""

    pass


class CloudbedsService:
    """Service wrapper for Cloudbeds API operations.

    Provides methods for fetching property and booking data from Cloudbeds
    using the cloudbeds-pms SDK with automatic token refresh.
    """

    def __init__(
        self,
        access_token: str | None = None,
        refresh_token: str | None = None,
    ) -> None:
        """Initialize CloudbedsService.

        Args:
            access_token: OAuth access token for API calls.
            refresh_token: OAuth refresh token for token renewal.
        """
        self._access_token = access_token
        self._refresh_token = refresh_token
        self._settings = get_settings()

    async def get_properties(self) -> list[dict[str, Any]]:
        """Fetch all properties from Cloudbeds.

        Returns:
            List of property dictionaries with id, name, and other details.

        Raises:
            CloudbedsServiceError: If API call fails.
        """
        if not self._access_token:
            msg = "Access token not configured"
            raise CloudbedsServiceError(msg)

        # TODO: Implement actual SDK call when cloudbeds-pms is available
        # For now, return placeholder to allow structure to be built
        logger.warning("CloudbedsService.get_properties: SDK not yet integrated")
        return []

    async def get_reservations(
        self,
        property_id: str,  # noqa: ARG002
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch reservations for a property.

        Args:
            property_id: Cloudbeds property ID.
            start_date: Filter reservations starting from this date.
            end_date: Filter reservations ending before this date.

        Returns:
            List of reservation dictionaries.

        Raises:
            CloudbedsServiceError: If API call fails.
        """
        if not self._access_token:
            msg = "Access token not configured"
            raise CloudbedsServiceError(msg)

        # Default date range: 24h ago to 365 days in future
        if start_date is None:
            start_date = datetime.now(UTC) - timedelta(hours=24)
        if end_date is None:
            end_date = datetime.now(UTC) + timedelta(days=365)

        # TODO: Implement actual SDK call when cloudbeds-pms is available
        logger.warning("CloudbedsService.get_reservations: SDK not yet integrated")
        return []

    async def refresh_access_token(self) -> tuple[str, str, datetime]:
        """Refresh the OAuth access token.

        Returns:
            Tuple of (new_access_token, new_refresh_token, expires_at).

        Raises:
            CloudbedsServiceError: If token refresh fails.
        """
        if not self._refresh_token:
            msg = "Refresh token not configured"
            raise CloudbedsServiceError(msg)

        # TODO: Implement actual token refresh when cloudbeds-pms is available
        logger.warning("CloudbedsService.refresh_access_token: SDK not yet integrated")
        msg = "Token refresh not yet implemented"
        raise CloudbedsServiceError(msg)

    @staticmethod
    def extract_phone_last4(phone: str | None) -> str | None:
        """Extract last 4 digits from a phone number.

        Args:
            phone: Full phone number string.

        Returns:
            Last 4 digits of phone number, or None if not available.
        """
        if not phone:
            return None

        # Remove non-digit characters and get last 4
        digits = "".join(c for c in phone if c.isdigit())
        if len(digits) >= PHONE_LAST_DIGITS:
            return digits[-PHONE_LAST_DIGITS:]
        return None
