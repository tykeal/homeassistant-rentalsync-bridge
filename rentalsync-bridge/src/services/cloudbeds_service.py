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

# Cloudbeds ``getReservations`` filters stays against the documented
# ``checkInFrom``/``checkOutTo`` window.  The lower bound must reach far
# enough back to include stays that began before the current sync but are
# still in progress, otherwise current reservations silently disappear.
DEFAULT_LOOKBACK_DAYS = 365
DEFAULT_LOOKAHEAD_DAYS = 365

# Cloudbeds ``getReservations`` paginates with ``pageNumber``/``pageSize``
# and documents 100 as the maximum (and default) page size.  Pages are
# requested until a short page signals the end of the results, bounded by
# ``MAX_RESERVATION_PAGES`` so a misbehaving API cannot loop forever.
RESERVATIONS_PAGE_SIZE = 100
MAX_RESERVATION_PAGES = 200

# The Cloudbeds v1.3 ``getReservations`` spec declares ``status`` as a
# SINGLE-VALUE enum (not_confirmed|confirmed|canceled|checked_in|checked_out|
# no_show), so the comma-separated list we used to send was not a valid
# value and its behaviour was undefined.  The parameter is therefore omitted
# entirely: the date window already bounds the result volume, and statuses
# are normalised locally by ``_normalize_status`` in the Cloudbeds provider.
# ``canceled``/``no_show`` must keep flowing through so cancellations can be
# propagated instead of silently vanishing from the response.
#
# ``not_confirmed`` is the one status we must drop client-side: it is an
# unfulfilled hold rather than a stay, so it is never a booking at all.
EXCLUDED_RESERVATION_STATUSES = frozenset({"not_confirmed"})


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
        """Fetch all properties from Cloudbeds using getHotels endpoint.

        Returns:
            List of property dictionaries with id, name, and timezone.

        Raises:
            CloudbedsServiceError: If API call fails.
        """
        auth_headers = self._get_auth_headers()

        async def fetch_hotels() -> list[dict[str, Any]]:
            """Fetch properties from Cloudbeds API."""
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    "https://api.cloudbeds.com/api/v1.3/getHotels",
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

                # Check for API success
                if not data.get("success"):
                    msg = f"API returned error: {data}"
                    raise CloudbedsServiceError(msg)

                # getHotels returns {"success": true, "data": [...]}
                hotels = data.get("data", [])
                if not hotels:
                    return []

                # Convert to standardized format
                return [
                    {
                        "propertyID": str(hotel.get("propertyID", "")),
                        "propertyName": hotel.get("propertyName", ""),
                        "propertyTimezone": hotel.get("propertyTimezone", "UTC"),
                    }
                    for hotel in hotels
                ]

        result: list[dict[str, Any]] = await self._with_retry(
            "get_properties", fetch_hotels
        )
        return result

    async def get_reservations(
        self,
        property_id: str,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch reservations for a property.

        No ``status`` filter is sent: the Cloudbeds v1.3 spec declares it as
        a single-value enum, so statuses are filtered client-side instead.

        Args:
            property_id: Cloudbeds property ID.
            start_date: Earliest check-in date to return, sent as
                ``checkInFrom`` (default: 365 days ago, so in-progress
                stays are included).
            end_date: Latest check-out date to return, sent as
                ``checkOutTo`` (default: 365 days ahead).

        Returns:
            List of reservation dictionaries, excluding ``not_confirmed``
            holds.  Cancellations are retained so they can be propagated.

        Raises:
            CloudbedsServiceError: If API call fails.
        """
        auth_headers = self._get_auth_headers()

        # Default date range: 365 days ago to 365 days in future
        if start_date is None:
            start_date = datetime.now(UTC) - timedelta(days=DEFAULT_LOOKBACK_DAYS)
        if end_date is None:
            end_date = datetime.now(UTC) + timedelta(days=DEFAULT_LOOKAHEAD_DAYS)

        async def fetch_reservations(page_number: int) -> list[dict[str, Any]]:
            """Fetch a single page of reservations from the Cloudbeds API.

            Args:
                page_number: 1-based results page to request.

            Returns:
                Reservations on the requested page.

            Raises:
                RateLimitError: If the API responds with HTTP 429.
                CloudbedsServiceError: If the API call fails.
            """
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    "https://api.cloudbeds.com/api/v1.3/getReservations",
                    headers={
                        **auth_headers,
                        "Accept": "application/json",
                    },
                    params={
                        "propertyID": property_id,
                        "checkInFrom": start_date.strftime("%Y-%m-%d"),
                        "checkOutTo": end_date.strftime("%Y-%m-%d"),
                        "includeAllRooms": "true",
                        "includeGuestsDetails": "true",
                        "pageNumber": str(page_number),
                        "pageSize": str(RESERVATIONS_PAGE_SIZE),
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

                if not data.get("success"):
                    msg = f"API returned error: {data}"
                    raise CloudbedsServiceError(msg)

                reservations: list[dict[str, Any]] = data.get("data", [])
                if reservations and page_number == 1:
                    # Log only the payload's field names: values may contain
                    # guest names, emails and phone numbers, and these logs
                    # are routinely pasted into public issue reports.
                    first_res = reservations[0]
                    room_keys = [k for k in first_res if "room" in k.lower()]
                    logger.debug(
                        "Reservation payload fields: %s (room-related keys: %s)",
                        sorted(first_res),
                        room_keys,
                    )
                return reservations

        # Accumulate every page before returning: the sync prune logic treats
        # an absent reservation as cancelled, so a partial snapshot must never
        # be returned.  Any page failure propagates and discards the whole run.
        all_reservations: list[dict[str, Any]] = []
        for page_number in range(1, MAX_RESERVATION_PAGES + 1):
            page: list[dict[str, Any]] = await self._with_retry(
                f"get_reservations (page {page_number})",
                fetch_reservations,
                page_number,
            )
            all_reservations.extend(
                r
                for r in page
                if str(r.get("status") or "").lower()
                not in EXCLUDED_RESERVATION_STATUSES
            )

            # Cloudbeds returns no pagination metadata, so a short page is the
            # documented end-of-results signal.  The RAW page length is used:
            # the client-side status filter above must not shorten a full page.
            if len(page) < RESERVATIONS_PAGE_SIZE:
                return all_reservations

        msg = (
            f"getReservations exceeded {MAX_RESERVATION_PAGES} pages for "
            f"property {property_id}; refusing to return a partial snapshot"
        )
        raise CloudbedsServiceError(msg)

    async def get_rooms(self, property_id: str) -> list[dict[str, Any]]:
        """Fetch rooms for a property from Cloudbeds API.

        Args:
            property_id: Cloudbeds property ID.

        Returns:
            List of room dictionaries with roomID, roomName, roomTypeName.

        Raises:
            CloudbedsServiceError: If API call fails.
        """
        auth_headers = self._get_auth_headers()

        async def fetch_rooms() -> list[dict[str, Any]]:
            """Fetch rooms from Cloudbeds API."""
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    "https://api.cloudbeds.com/api/v1.3/getRooms",
                    headers={
                        **auth_headers,
                        "Accept": "application/json",
                    },
                    params={
                        "propertyIDs": property_id,
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

                if not data.get("success"):
                    msg = f"API returned error: {data}"
                    raise CloudbedsServiceError(msg)

                # Response structure: {"data": [{"propertyID": "...", "rooms": [...]}]}
                # Extract rooms from the nested structure
                rooms: list[dict[str, Any]] = []
                properties_data = data.get("data", [])
                for prop in properties_data:
                    if isinstance(prop, dict) and "rooms" in prop:
                        rooms.extend(prop["rooms"])
                if rooms:
                    # Field names only: room payloads are provider data that
                    # may carry free-text values, and these logs are pasted
                    # into public issue reports.
                    logger.debug(
                        "Room payload fields for property %s: %s",
                        property_id,
                        sorted(rooms[0]) if isinstance(rooms[0], dict) else [],
                    )
                logger.info("Fetched %d rooms for property %s", len(rooms), property_id)
                return rooms

        result: list[dict[str, Any]] = await self._with_retry("get_rooms", fetch_rooms)
        return result

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
