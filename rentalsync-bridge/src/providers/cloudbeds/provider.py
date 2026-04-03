# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Cloudbeds PMS provider — wraps CloudbedsService behind PMSProvider ABC."""

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

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
from src.services.cloudbeds_service import (
    CloudbedsService,
    CloudbedsServiceError,
    RateLimitError,
)

if TYPE_CHECKING:
    from src.models.oauth_credential import OAuthCredential

logger = logging.getLogger(__name__)


class CloudbedsProvider(PMSProvider):
    """PMSProvider implementation backed by CloudbedsService."""

    def __init__(
        self,
        access_token: str | None = None,
        refresh_token: str | None = None,
        api_key: str | None = None,
        **_kwargs: Any,
    ) -> None:
        """Initialize CloudbedsProvider.

        Args:
            access_token: OAuth access token for API calls.
            refresh_token: OAuth refresh token for token renewal.
            api_key: API key for authentication.
            **_kwargs: Ignored — accepted for registry compatibility.
        """
        self._service = CloudbedsService(
            access_token=access_token,
            refresh_token=refresh_token,
            api_key=api_key,
        )

    # -- helpers ---------------------------------------------------------------

    @staticmethod
    def _translate_error(exc: CloudbedsServiceError) -> PMSProviderError:
        """Convert a CloudbedsServiceError to the appropriate PMS error.

        Args:
            exc: Original Cloudbeds exception.

        Returns:
            Corresponding PMSProviderError subclass instance.
        """
        if isinstance(exc, RateLimitError):
            return PMSRateLimitError(str(exc), retry_after=exc.retry_after)

        msg = str(exc).lower()
        if "authentication" in msg or "unauthorized" in msg or "401" in msg:
            return PMSAuthenticationError(str(exc))
        if "connection" in msg or "network" in msg or "timeout" in msg:
            return PMSConnectionError(str(exc))
        return PMSProviderError(str(exc))

    # -- PMSProvider interface -------------------------------------------------

    @property
    def provider_type(self) -> str:
        """Return the provider type identifier string.

        Returns:
            Literal ``"cloudbeds"``.
        """
        return "cloudbeds"

    async def get_listings(self) -> list[PMSListing]:
        """Fetch all properties from Cloudbeds.

        Returns:
            List of PMSListing DTOs (rooms left empty).

        Raises:
            PMSProviderError: On API failure.
        """
        try:
            properties = await self._service.get_properties()
        except CloudbedsServiceError as exc:
            raise self._translate_error(exc) from exc

        return [
            PMSListing(
                pms_id=prop["propertyID"],
                name=prop["propertyName"],
                timezone=prop.get("propertyTimezone", "UTC"),
            )
            for prop in properties
        ]

    async def get_reservations(
        self,
        listing_pms_id: str,
        *,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> list[PMSReservation]:
        """Fetch reservations for a Cloudbeds property.

        Args:
            listing_pms_id: Cloudbeds property ID.
            start_date: Start filter (default 24 h ago).
            end_date: End filter (default 365 days ahead).

        Returns:
            List of PMSReservation DTOs.

        Raises:
            PMSProviderError: On API failure.
        """
        try:
            raw = await self._service.get_reservations(
                property_id=listing_pms_id,
                start_date=start_date,
                end_date=end_date,
            )
        except CloudbedsServiceError as exc:
            raise self._translate_error(exc) from exc

        return [self._map_reservation(r, listing_pms_id) for r in raw]

    async def get_rooms(self, listing_pms_id: str) -> list[PMSRoom]:
        """Fetch rooms for a Cloudbeds property.

        Args:
            listing_pms_id: Cloudbeds property ID.

        Returns:
            List of PMSRoom DTOs.

        Raises:
            PMSProviderError: On API failure.
        """
        try:
            raw = await self._service.get_rooms(listing_pms_id)
        except CloudbedsServiceError as exc:
            raise self._translate_error(exc) from exc

        return [
            PMSRoom(
                pms_room_id=str(room.get("roomID", "")),
                name=room.get("roomName", ""),
                room_type=room.get("roomTypeName"),
            )
            for room in raw
        ]

    async def get_guest(self, guest_id: str) -> PMSGuest | None:
        """Return guest data by searching live reservation data.

        Cloudbeds embeds guest info in reservations; this method
        makes live API calls across all properties to find the
        matching guest.

        Args:
            guest_id: Cloudbeds guest ID.

        Returns:
            PMSGuest if found, else None.

        Raises:
            PMSProviderError: If API communication fails for all properties.
        """
        try:
            properties = await self._service.get_properties()
        except CloudbedsServiceError as exc:
            raise self._translate_error(exc) from exc

        last_error: CloudbedsServiceError | None = None
        properties_failed = 0

        for prop in properties:
            try:
                reservations = await self._service.get_reservations(
                    property_id=prop["propertyID"],
                    start_date=datetime.now(UTC) - timedelta(days=365),
                    end_date=datetime.now(UTC) + timedelta(days=365),
                )
            except CloudbedsServiceError as exc:
                properties_failed += 1
                last_error = exc
                logger.warning(
                    "Failed to fetch reservations for property %s: %s",
                    prop["propertyID"],
                    exc,
                )
                continue

            for res in reservations:
                if str(res.get("guestID", "")) == guest_id:
                    return PMSGuest(
                        guest_id=guest_id,
                        full_name=res.get("guestName", ""),
                        phone=res.get("guestPhone"),
                        email=res.get("guestEmail"),
                    )

        if properties_failed == len(properties) and last_error is not None:
            raise self._translate_error(last_error) from last_error

        return None

    async def get_custom_fields(self, reservation_id: str) -> dict[str, Any]:
        """Extract custom_data from Cloudbeds reservation.

        Cloudbeds returns custom fields inline with the reservation
        payload, so we search reservations for the matching ID.

        Args:
            reservation_id: Cloudbeds reservation/booking ID.

        Returns:
            Dictionary of custom field values.

        Raises:
            PMSProviderError: If API communication fails for all properties.
        """
        # TODO(optimization): Cloudbeds API does not support fetching a
        # single reservation by ID across properties.  This iterates all
        # properties which is expensive for multi-property accounts.
        # Consider caching or a reservation→property mapping table.
        try:
            properties = await self._service.get_properties()
        except CloudbedsServiceError as exc:
            raise self._translate_error(exc) from exc

        last_error: CloudbedsServiceError | None = None
        properties_failed = 0

        for prop in properties:
            try:
                reservations = await self._service.get_reservations(
                    property_id=prop["propertyID"],
                )
            except CloudbedsServiceError as exc:
                properties_failed += 1
                last_error = exc
                logger.warning(
                    "Failed to fetch reservations for property %s: %s",
                    prop["propertyID"],
                    exc,
                )
                continue

            for res in reservations:
                if str(res.get("reservationID", "")) == reservation_id:
                    return dict(res.get("customFields", {}))

        if properties_failed == len(properties) and last_error is not None:
            raise self._translate_error(last_error) from last_error

        return {}

    async def refresh_token(
        self,
        credential: "OAuthCredential",  # noqa: ARG002
    ) -> TokenResult:
        """Refresh the OAuth token via CloudbedsService.

        Note:
            Cloudbeds handles token refresh differently from other providers.
            The underlying ``CloudbedsService.refresh_access_token()`` manages
            the OAuth authorization-code flow internally.  Direct token refresh
            via this method is not yet implemented — it currently delegates to
            the service which may raise if the refresh flow has not been set up.
            For production use, token refresh is handled by the existing
            ``OAuthService`` outside the provider layer.

        Args:
            credential: Current credential record (unused; kept for
                interface compatibility).

        Returns:
            TokenResult with new token data.

        Raises:
            PMSProviderError: If refresh fails or is not supported.
        """
        try:
            (
                access_token,
                new_refresh,
                expires_at,
            ) = await self._service.refresh_access_token()
        except CloudbedsServiceError as exc:
            raise self._translate_error(exc) from exc

        return TokenResult(
            access_token=access_token,
            refresh_token=new_refresh,
            expires_at=expires_at,
        )

    async def test_connection(self) -> bool:
        """Test Cloudbeds API reachability.

        Returns:
            True if ``get_properties()`` succeeds.

        Raises:
            PMSProviderError: On connection failure.
        """
        try:
            await self._service.get_properties()
        except CloudbedsServiceError as exc:
            raise self._translate_error(exc) from exc
        return True

    # -- private mapping -------------------------------------------------------

    @staticmethod
    def _map_reservation(raw: dict[str, Any], listing_pms_id: str) -> PMSReservation:
        """Convert a Cloudbeds reservation dict to a PMSReservation.

        Args:
            raw: Raw reservation dict from Cloudbeds API.
            listing_pms_id: Owning property ID.

        Returns:
            PMSReservation DTO.
        """
        room_ids: list[str] = []
        # Cloudbeds nests assigned rooms under various keys
        for key in ("rooms", "assigned"):
            rooms_data = raw.get(key)
            if isinstance(rooms_data, list):
                for r in rooms_data:
                    rid = r.get("roomID") or r.get("roomId")
                    if rid is not None:
                        room_ids.append(str(rid))
        if not room_ids and raw.get("roomID"):
            room_ids.append(str(raw["roomID"]))

        check_in = raw.get("startDate") or raw.get("checkInDate", "")
        check_out = raw.get("endDate") or raw.get("checkOutDate", "")

        return PMSReservation(
            pms_booking_id=str(raw.get("reservationID", "")),
            listing_pms_id=listing_pms_id,
            guest_name=raw.get("guestName"),
            guest_id=str(raw["guestID"]) if raw.get("guestID") else None,
            check_in=_parse_date(check_in),
            check_out=_parse_date(check_out),
            status=raw.get("status", "confirmed"),
            room_ids=tuple(room_ids),
            custom_data=dict(raw.get("customFields", {})),
        )


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
        return value if value.tzinfo else value.replace(tzinfo=UTC)

    try:
        dt = datetime.fromisoformat(value)
    except (ValueError, TypeError):
        try:
            dt = datetime.strptime(value, "%Y-%m-%d")
        except (ValueError, TypeError) as exc:
            msg = f"Cannot parse date value: {value!r}"
            raise PMSProviderError(msg) from exc
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
