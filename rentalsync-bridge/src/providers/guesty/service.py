# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Guesty PMS provider — full Guesty Open API integration."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import httpx

from src.providers.base import (
    PMSAuthenticationError,
    PMSConnectionError,
    PMSGuest,
    PMSListing,
    PMSProvider,
    PMSProviderError,
    PMSRateLimitError,
    PMSReservation,
    PMSRoom,
    TokenResult,
)
from src.providers.guesty.auth import GUESTY_TOKEN_URL, GuestyTokenManager
from src.providers.registry import provider

if TYPE_CHECKING:
    from src.models.oauth_credential import OAuthCredential
    from src.repositories.credential_repository import CredentialRepository

logger = logging.getLogger(__name__)

GUESTY_BASE_URL = "https://open-api.guesty.com"
DEFAULT_PAGE_LIMIT = 100
MAX_RETRIES = 3
INITIAL_BACKOFF = 1.0
MAX_BACKOFF = 30.0

# Guesty statuses that map to our normalised reservation statuses.
_STATUS_MAP: dict[str, str] = {
    "confirmed": "confirmed",
    "checked_in": "confirmed",
    "checked_out": "checked_out",
    "canceled": "cancelled",
}

# Guesty statuses to exclude from results.
_EXCLUDED_STATUSES = frozenset({"inquiry", "reserved"})


@provider("guesty")
class GuestyProvider(PMSProvider):
    """PMSProvider implementation backed by the Guesty Open API."""

    def __init__(
        self,
        credential_repo: "CredentialRepository | None" = None,
        credential_id: int | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
        token_manager: GuestyTokenManager | None = None,
        http_client: httpx.AsyncClient | None = None,
        **_kwargs: Any,
    ) -> None:
        """Initialize GuestyProvider.

        Args:
            credential_repo: Repository for credential persistence.
            credential_id: Database ID of the credential row.
            client_id: Guesty OAuth2 client ID.
            client_secret: Guesty OAuth2 client secret.
            token_manager: Optional pre-configured token manager.
            http_client: Optional httpx client for API calls.
            **_kwargs: Ignored — accepted for registry compatibility.
        """
        self._http_client = http_client
        self._owns_client = http_client is None

        self._token_manager: GuestyTokenManager | None
        if token_manager is not None:
            self._token_manager = token_manager
        elif (
            credential_repo is not None
            and credential_id is not None
            and client_id is not None
            and client_secret is not None
        ):
            self._token_manager = GuestyTokenManager(
                client_id=client_id,
                client_secret=client_secret,
                credential_repo=credential_repo,
                credential_id=credential_id,
                http_client=http_client,
            )
        else:
            self._token_manager = None

    async def aclose(self) -> None:
        """Close the owned httpx client, if any."""
        if self._owns_client and self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None

    async def __aenter__(self) -> "GuestyProvider":
        """Enter the async context manager."""
        return self

    async def __aexit__(self, *args: object) -> None:
        """Exit the async context manager."""
        await self.aclose()

    # -- helpers ---------------------------------------------------------------

    def _get_client(self) -> httpx.AsyncClient:
        """Get or create an httpx AsyncClient.

        Returns:
            An httpx.AsyncClient instance.
        """
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(
                base_url=GUESTY_BASE_URL,
                timeout=httpx.Timeout(30.0),
            )
            self._owns_client = True
        return self._http_client

    async def _get_auth_headers(self) -> dict[str, str]:
        """Build authorization headers using the token manager.

        Returns:
            Dict with Authorization header.

        Raises:
            PMSAuthenticationError: If no token manager is configured.
        """
        if self._token_manager is None:
            msg = "GuestyProvider requires a token manager"
            raise PMSAuthenticationError(msg)
        token = await self._token_manager.get_token()
        return {"Authorization": f"Bearer {token}"}

    async def _request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        raise_on_404: bool = True,
    ) -> httpx.Response:
        """Make an HTTP request with retry logic for 429 rate limits.

        Implements exponential backoff (1s → 2s → 4s, max 30s) and
        respects the Retry-After header.  Max 3 retries.

        Args:
            method: HTTP method (GET, POST, etc.).
            url: Request URL (relative to base URL).
            params: Optional query parameters.
            raise_on_404: If False, return the 404 response instead
                of raising.  Defaults to True.

        Returns:
            The httpx.Response object.

        Raises:
            PMSRateLimitError: If retries are exhausted on 429.
            PMSAuthenticationError: On 401/403 responses.
            PMSConnectionError: On network-level failures.
            PMSProviderError: On other HTTP errors.
        """
        client = self._get_client()
        headers = await self._get_auth_headers()
        backoff = INITIAL_BACKOFF

        for attempt in range(MAX_RETRIES + 1):
            try:
                response = await client.request(
                    method,
                    url,
                    params=params,
                    headers=headers,
                )
            except httpx.HTTPError as exc:
                msg = f"Network error calling Guesty API: {exc}"
                raise PMSConnectionError(msg) from exc

            if response.status_code == 429:  # noqa: PLR2004
                if attempt >= MAX_RETRIES:
                    retry_after = _parse_retry_after(response)
                    msg = f"Guesty API rate limit exceeded after {MAX_RETRIES} retries"
                    raise PMSRateLimitError(msg, retry_after=retry_after)

                retry_after = _parse_retry_after(response)
                wait = min(
                    retry_after if retry_after is not None else backoff, MAX_BACKOFF
                )
                logger.warning(
                    "Guesty API rate limited (429), retrying in %.1fs (attempt %d/%d)",
                    wait,
                    attempt + 1,
                    MAX_RETRIES,
                )
                await asyncio.sleep(wait)
                backoff = min(backoff * 2, MAX_BACKOFF)
                continue

            if response.status_code in (401, 403):
                msg = (
                    f"Guesty authentication failed "
                    f"(HTTP {response.status_code}): {response.text}"
                )
                raise PMSAuthenticationError(msg)

            if response.status_code == 404 and not raise_on_404:  # noqa: PLR2004
                return response

            if response.status_code >= 400:  # noqa: PLR2004
                msg = f"Guesty API error (HTTP {response.status_code}): {response.text}"
                raise PMSProviderError(msg)

            return response

        # Should not be reached, but satisfy type checker
        msg = "Request loop exited without returning"  # pragma: no cover
        raise PMSProviderError(msg)  # pragma: no cover

    # -- PMSProvider interface -------------------------------------------------

    @property
    def provider_type(self) -> str:
        """Return the provider type identifier string.

        Returns:
            Literal ``"guesty"``.
        """
        return "guesty"

    async def get_listings(self) -> list[PMSListing]:
        """Fetch all listings from the Guesty API.

        Handles skip-based pagination (limit=100).  Stops when
        the number of results returned is less than the limit.

        Returns:
            List of PMSListing DTOs.

        Raises:
            PMSProviderError: On API failure.
        """
        listings: list[PMSListing] = []
        skip = 0

        while True:
            response = await self._request(
                "GET",
                "/v1/listings",
                params={"limit": DEFAULT_PAGE_LIMIT, "skip": skip},
            )
            data = response.json()
            results = data.get("results", [])

            for item in results:
                pms_id = str(item.get("_id", ""))
                if not pms_id:
                    logger.warning("Skipping Guesty listing with missing _id")
                    continue
                listings.append(
                    PMSListing(
                        pms_id=pms_id,
                        name=item.get("title") or item.get("nickname") or "",
                        timezone=item.get("timezone") or "UTC",
                        address=_format_address(item.get("address")),
                    )
                )

            if len(results) < DEFAULT_PAGE_LIMIT:
                break
            skip += DEFAULT_PAGE_LIMIT

        return listings

    async def get_reservations(
        self,
        listing_pms_id: str,
        *,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> list[PMSReservation]:
        """Fetch reservations for a Guesty listing.

        Args:
            listing_pms_id: Guesty listing ID.
            start_date: Start filter (default: 24h ago).
            end_date: End filter (default: 365 days ahead).

        Returns:
            List of PMSReservation DTOs.

        Raises:
            PMSProviderError: On API failure.
        """
        if start_date is None:
            start_date = datetime.now(UTC) - timedelta(hours=24)
        if end_date is None:
            end_date = datetime.now(UTC) + timedelta(days=365)

        statuses = "confirmed,checked_in,checked_out,canceled"
        reservations: list[PMSReservation] = []
        skip = 0

        while True:
            params: dict[str, Any] = {
                "listingId": listing_pms_id,
                "status": statuses,
                "limit": DEFAULT_PAGE_LIMIT,
                "skip": skip,
                "checkIn": start_date.strftime("%Y-%m-%d"),
                "checkOut": end_date.strftime("%Y-%m-%d"),
            }
            response = await self._request("GET", "/v1/reservations", params=params)
            data = response.json()
            results = data.get("results", [])

            for item in results:
                mapped = self._map_reservation(item, listing_pms_id)
                if mapped is not None:
                    reservations.append(mapped)

            if len(results) < DEFAULT_PAGE_LIMIT:
                break
            skip += DEFAULT_PAGE_LIMIT

        return reservations

    async def get_rooms(self, listing_pms_id: str) -> list[PMSRoom]:
        """Fetch rooms/units for a Guesty listing.

        For multi-unit listings, maps ``childListings`` to PMSRoom.
        For single-unit listings, creates one implicit PMSRoom.

        Args:
            listing_pms_id: Guesty listing ID.

        Returns:
            List of PMSRoom DTOs.

        Raises:
            PMSProviderError: On API failure.
        """
        response = await self._request(
            "GET",
            f"/v1/listings/{listing_pms_id}",
        )
        data = response.json()

        children = data.get("childListings") or []
        if children:
            rooms: list[PMSRoom] = []
            for child in children:
                child_id = str(child.get("_id", ""))
                if not child_id:
                    continue
                rooms.append(
                    PMSRoom(
                        pms_room_id=child_id,
                        name=child.get("title") or child.get("nickname") or "",
                        room_type=child.get("roomType"),
                    )
                )
            return rooms

        # Single-unit: create one implicit room
        return [
            PMSRoom(
                pms_room_id=listing_pms_id,
                name=data.get("title") or data.get("nickname") or "Main",
                room_type=data.get("propertyType"),
            )
        ]

    async def get_guest(self, guest_id: str) -> PMSGuest | None:
        """Fetch guest details from the Guesty API.

        Args:
            guest_id: Guesty guest ID.

        Returns:
            PMSGuest if found, else None (on 404).

        Raises:
            PMSProviderError: On non-404 API failures.
        """
        response = await self._request(
            "GET",
            f"/v1/guests/{guest_id}",
            raise_on_404=False,
        )

        if response.status_code == 404:  # noqa: PLR2004
            return None

        data = response.json()
        return PMSGuest(
            guest_id=guest_id,
            full_name=data.get("fullName") or "",
            phone=data.get("phone"),
            email=data.get("email"),
        )

    async def get_custom_fields(
        self,
        reservation_id: str,
    ) -> dict[str, Any]:
        """Fetch custom fields for a Guesty reservation.

        Uses the v3 endpoint: GET /v1/reservations-v3/{id}/custom-fields.

        Args:
            reservation_id: Guesty reservation ID.

        Returns:
            Dictionary of fieldId → value mappings.

        Raises:
            PMSProviderError: On API failure.
        """
        response = await self._request(
            "GET",
            f"/v1/reservations-v3/{reservation_id}/custom-fields",
        )
        data = response.json()

        # Response is a list of {fieldId, value} objects
        if isinstance(data, list):
            return {
                item["fieldId"]: item.get("value")
                for item in data
                if isinstance(item, dict) and "fieldId" in item
            }

        # Fallback if response is a dict with "results" or similar
        results = data.get("results", data.get("customFields", []))
        if isinstance(results, list):
            return {
                item["fieldId"]: item.get("value")
                for item in results
                if isinstance(item, dict) and "fieldId" in item
            }

        return {}

    async def refresh_token(
        self,
        credential: "OAuthCredential",
    ) -> TokenResult:
        """Refresh the Guesty OAuth token.

        Guesty uses client-credentials flow, so "refresh" means
        requesting a new token.  Delegates to the token manager when
        available; otherwise falls back to a direct HTTP request using
        the credential's ``client_id`` / ``client_secret`` (e.g. when
        constructed by ``OAuthService`` with no kwargs).

        Args:
            credential: Current credential record.

        Returns:
            TokenResult with new access token and expiry.

        Raises:
            PMSAuthenticationError: If token request fails.
            PMSConnectionError: If unable to reach Guesty API.
        """
        if self._token_manager is not None:
            self._token_manager.invalidate_cache()
            token = await self._token_manager.get_token()
            expires_at = self._token_manager.cached_expires_at or datetime.now(UTC)
            return TokenResult(
                access_token=token,
                refresh_token=None,
                expires_at=expires_at,
            )

        # No token manager — direct client-credentials request
        if not credential.client_id or not credential.client_secret:
            msg = "Guesty refresh requires client_id and client_secret"
            raise PMSAuthenticationError(msg)

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
                response = await client.post(
                    GUESTY_TOKEN_URL,
                    data={
                        "grant_type": "client_credentials",
                        "scope": "open-api",
                        "client_id": credential.client_id,
                        "client_secret": credential.client_secret,
                    },
                )
        except httpx.HTTPError as exc:
            msg = f"Failed to connect to Guesty OAuth endpoint: {exc}"
            raise PMSConnectionError(msg) from exc

        if response.status_code in (401, 403):
            msg = (
                f"Guesty authentication failed "
                f"(HTTP {response.status_code}): {response.text}"
            )
            raise PMSAuthenticationError(msg)

        if response.status_code != 200:  # noqa: PLR2004
            msg = (
                f"Guesty token request failed "
                f"(HTTP {response.status_code}): {response.text}"
            )
            raise PMSAuthenticationError(msg)

        data = response.json()
        access_token = data.get("access_token")
        if not access_token:
            msg = "Token response missing access_token"
            raise PMSAuthenticationError(msg)

        expires_in = data.get("expires_in", 86400)
        try:
            expires_in = int(expires_in)
        except (TypeError, ValueError):
            expires_in = 86400  # Default 24h

        return TokenResult(
            access_token=access_token,
            refresh_token=None,
            expires_at=datetime.now(UTC) + timedelta(seconds=expires_in),
        )

    async def test_connection(self) -> bool:
        """Test Guesty API reachability.

        Calls ``get_listings()`` and verifies the API responds.
        Zero listings produces a warning but is not a failure.

        Returns:
            True if the API responds successfully.

        Raises:
            PMSProviderError: On connection failure.
        """
        listings = await self.get_listings()
        if not listings:
            logger.warning(
                "Guesty connection test succeeded but returned "
                "zero listings — verify account configuration"
            )
        return True

    # -- private mapping -------------------------------------------------------

    @staticmethod
    def _map_reservation(
        raw: dict[str, Any],
        listing_pms_id: str,
    ) -> PMSReservation | None:
        """Convert a Guesty reservation dict to a PMSReservation.

        Args:
            raw: Raw reservation dict from Guesty API.
            listing_pms_id: Owning listing ID.

        Returns:
            PMSReservation DTO, or None if the status should be
            excluded.
        """
        status_raw = (raw.get("status") or "").lower()

        if status_raw in _EXCLUDED_STATUSES:
            return None

        status = _STATUS_MAP.get(status_raw, status_raw)

        pms_booking_id = str(raw.get("_id", ""))
        if not pms_booking_id:
            logger.warning("Skipping Guesty reservation with missing _id")
            return None

        guest = raw.get("guest", {}) or {}
        guest_name = guest.get("fullName")
        guest_id = str(guest.get("_id", "")) or None

        check_in_raw = raw.get("checkIn") or raw.get("checkInDateLocalized", "")
        check_out_raw = raw.get("checkOut") or raw.get("checkOutDateLocalized", "")

        try:
            check_in = _parse_date(check_in_raw)
            check_out = _parse_date(check_out_raw)
        except PMSProviderError:
            logger.warning(
                "Skipping reservation %s with unparseable dates",
                pms_booking_id,
            )
            return None

        # Room IDs: Guesty may embed listingId as the room reference
        room_id = str(raw.get("listingId") or listing_pms_id)

        return PMSReservation(
            pms_booking_id=pms_booking_id,
            listing_pms_id=listing_pms_id,
            guest_name=guest_name,
            guest_id=guest_id,
            check_in=check_in,
            check_out=check_out,
            status=status,
            room_ids=(room_id,),
            custom_data={},
        )


# -- module-level helpers ------------------------------------------------------


def _parse_date(value: str | datetime | None) -> datetime:
    """Parse a date string or pass through a datetime.

    Args:
        value: ISO-format date string, datetime, or None/empty.

    Returns:
        Timezone-aware datetime (UTC).

    Raises:
        PMSProviderError: If *value* is falsy or cannot be parsed.
    """
    if not value:
        msg = f"Cannot parse date from empty or None value: {value!r}"
        raise PMSProviderError(msg)

    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)

    if value.endswith("Z"):
        value = value[:-1] + "+00:00"

    try:
        dt = datetime.fromisoformat(value)
    except (ValueError, TypeError):
        try:
            dt = datetime.strptime(value, "%Y-%m-%d")
        except (ValueError, TypeError) as exc:
            msg = f"Cannot parse date value: {value!r}"
            raise PMSProviderError(msg) from exc
    return dt.astimezone(UTC) if dt.tzinfo else dt.replace(tzinfo=UTC)


def _parse_retry_after(response: httpx.Response) -> float | None:
    """Parse the Retry-After header from an HTTP response.

    Args:
        response: The httpx response to inspect.

    Returns:
        Seconds to wait, or None if header is absent/unparseable.
    """
    header = response.headers.get("Retry-After")
    if header is None:
        return None
    try:
        return float(header)
    except (ValueError, TypeError):
        return None


def _format_address(address: dict[str, Any] | None) -> str | None:
    """Format a Guesty address object into a string.

    Args:
        address: Guesty address dict with street, city, state, etc.

    Returns:
        Formatted address string, or None if no address data.
    """
    if not address:
        return None

    full = address.get("full")
    if full:
        return str(full)

    parts = [
        address.get("street"),
        address.get("city"),
        address.get("state"),
        address.get("zipcode"),
        address.get("country"),
    ]
    formatted = ", ".join(str(p) for p in parts if p)
    return formatted or None
