# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Cloudbeds API service wrapper using cloudbeds-pms SDK."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from typing import Any

import httpx

from src.config import get_settings

logger = logging.getLogger(__name__)


PHONE_LAST_DIGITS = 4
MAX_RETRIES = 3
BASE_DELAY_SECONDS = 1.0
MAX_DELAY_SECONDS = 30.0


class CloudbedsServiceError(Exception):
    """Exception raised for Cloudbeds API errors."""

    pass


class RateLimitError(CloudbedsServiceError):
    """Exception raised when API rate limit is exceeded."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        """Initialize RateLimitError.

        Args:
            message: Error message.
            retry_after: Seconds to wait before retry, if provided by API.
        """
        super().__init__(message)
        self.retry_after = retry_after


class CloudbedsService:
    """Service wrapper for Cloudbeds API operations.

    Provides methods for fetching property and booking data from Cloudbeds
    using the cloudbeds-pms SDK with automatic token refresh and rate limit
    handling with exponential backoff.
    """

    def __init__(
        self,
        access_token: str | None = None,
        refresh_token: str | None = None,
        api_key: str | None = None,
    ) -> None:
        """Initialize CloudbedsService.

        Args:
            access_token: OAuth access token for API calls.
            refresh_token: OAuth refresh token for token renewal.
            api_key: API key for authentication (alternative to OAuth).
        """
        self._access_token = access_token
        self._refresh_token = refresh_token
        self._api_key = api_key
        self._settings = get_settings()

    def _get_auth_headers(self) -> dict[str, str]:
        """Get authentication headers for API calls.

        Returns:
            Dict with Authorization header using either Bearer token or API key.

        Raises:
            CloudbedsServiceError: If no authentication is configured.
        """
        if self._access_token:
            return {"Authorization": f"Bearer {self._access_token}"}
        if self._api_key:
            return {"Authorization": f"Bearer {self._api_key}"}
        msg = "No authentication configured (access_token or api_key required)"
        raise CloudbedsServiceError(msg)

    async def _with_retry(
        self,
        operation: str,
        func: Any,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """Execute an API call with exponential backoff retry on rate limit.

        Args:
            operation: Description of operation for logging.
            func: Async function to call.
            *args: Positional arguments for func.
            **kwargs: Keyword arguments for func.

        Returns:
            Result from func.

        Raises:
            CloudbedsServiceError: If all retries fail.
        """
        last_error: Exception | None = None
        delay = BASE_DELAY_SECONDS

        for attempt in range(MAX_RETRIES + 1):
            try:
                return await func(*args, **kwargs)
            except RateLimitError as e:
                last_error = e
                if attempt == MAX_RETRIES:
                    break

                # Use retry_after if provided, otherwise exponential backoff
                wait_time = e.retry_after if e.retry_after else delay
                wait_time = min(wait_time, MAX_DELAY_SECONDS)

                logger.warning(
                    "%s rate limited, retrying in %.1fs (attempt %d/%d)",
                    operation,
                    wait_time,
                    attempt + 1,
                    MAX_RETRIES,
                )
                await asyncio.sleep(wait_time)
                delay *= 2  # Exponential backoff

        msg = f"{operation} failed after {MAX_RETRIES} retries: {last_error}"
        raise CloudbedsServiceError(msg) from last_error

    async def get_properties(self) -> list[dict[str, Any]]:
        """Fetch all properties from Cloudbeds using OAuth metadata.

        The OAuth metadata endpoint returns property info associated with the token.

        Returns:
            List of property dictionaries with id, name, and timezone.

        Raises:
            CloudbedsServiceError: If API call fails.
        """
        auth_headers = self._get_auth_headers()

        async def fetch_metadata() -> list[dict[str, Any]]:
            """Fetch property metadata from Cloudbeds API."""
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    "https://api.cloudbeds.com/api/v1.3/oauth/metadata",
                    headers={
                        **auth_headers,
                        "Accept": "application/json",
                    },
                    timeout=30.0,
                )

                if response.status_code == HTTPStatus.TOO_MANY_REQUESTS:
                    retry_after = response.headers.get("Retry-After")
                    raise RateLimitError(
                        "Rate limited",
                        retry_after=float(retry_after) if retry_after else None,
                    )

                if response.status_code != HTTPStatus.OK:
                    msg = f"API error: {response.status_code} {response.text}"
                    raise CloudbedsServiceError(msg)

                data = response.json()

                # The metadata response contains property info
                # Structure: {"success": true, "data": {"propertyID": "...", ...}}
                if not data.get("success"):
                    msg = f"API returned error: {data}"
                    raise CloudbedsServiceError(msg)

                prop_data = data.get("data", {})
                if not prop_data:
                    return []

                # Convert to list format (API returns single property per token)
                return [
                    {
                        "propertyID": prop_data.get("propertyID"),
                        "propertyName": prop_data.get("propertyName"),
                        "propertyTimezone": prop_data.get("propertyTimezone"),
                    }
                ]

        result: list[dict[str, Any]] = await self._with_retry(
            "get_properties", fetch_metadata
        )
        return result

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
