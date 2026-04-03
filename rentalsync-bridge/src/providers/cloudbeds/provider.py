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
from src.providers.registry import provider
from src.services.cloudbeds_service import (
    CloudbedsService,
    CloudbedsServiceError,
    RateLimitError,
)

if TYPE_CHECKING:
    from src.models.oauth_credential import OAuthCredential

logger = logging.getLogger(__name__)


@provider("cloudbeds")
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
    # TODO: factor repeated try/except CloudbedsServiceError + generic
    # Exception wrapping into a small async context-manager or decorator
    # to reduce boilerplate across provider methods.

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
        except Exception as exc:
            raise PMSProviderError(f"Unexpected error from Cloudbeds: {exc}") from exc

        listings: list[PMSListing] = []
        for prop in properties:
            raw_id = prop.get("propertyID")
            pms_id = str(raw_id) if raw_id is not None else ""
            if not pms_id:
                logger.warning("Skipping Cloudbeds property with missing ID")
                continue
            listings.append(
                PMSListing(
                    pms_id=pms_id,
                    name=prop.get("propertyName") or "",
                    timezone=prop.get("propertyTimezone") or "UTC",
                )
            )
        return listings

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
        except Exception as exc:
            raise PMSProviderError(f"Unexpected error from Cloudbeds: {exc}") from exc

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
        except Exception as exc:
            raise PMSProviderError(f"Unexpected error from Cloudbeds: {exc}") from exc

        rooms: list[PMSRoom] = []
        for room in raw:
            # Cloudbeds uses both roomID and roomId keys
            raw_room_id = room.get("roomID")
            if raw_room_id is None:
                raw_room_id = room.get("roomId")
            room_id = str(raw_room_id) if raw_room_id is not None else ""
            if not room_id:
                logger.warning(
                    "Skipping Cloudbeds room with missing ID in listing %s",
                    listing_pms_id,
                )
                continue
            rooms.append(
                PMSRoom(
                    pms_room_id=room_id,
                    name=room.get("roomName") or "",
                    room_type=room.get("roomTypeName"),
                )
            )
        return rooms

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
        except Exception as exc:
            raise PMSProviderError(f"Unexpected error from Cloudbeds: {exc}") from exc

        # TODO(optimization): Cloudbeds API does not support fetching a
        # single guest by ID across properties.  This iterates all
        # properties sequentially which is expensive for multi-property
        # accounts.  Consider caching or a guest→property mapping table.
        last_error: Exception | None = None
        properties_failed = 0
        now = datetime.now(UTC)

        for prop in properties:
            prop_id = prop.get("propertyID", "")
            if not prop_id:
                logger.warning("Skipping property with blank ID in get_guest")
                continue
            try:
                reservations = await self._service.get_reservations(
                    property_id=prop_id,
                    start_date=now - timedelta(days=365),
                    end_date=now + timedelta(days=365),
                )
            except CloudbedsServiceError as exc:
                properties_failed += 1
                last_error = exc
                logger.warning(
                    "Failed to fetch reservations for property %s: %s",
                    prop_id,
                    exc,
                )
                continue
            except Exception as exc:
                properties_failed += 1
                last_error = exc
                logger.warning(
                    "Unexpected error fetching reservations for property %s: %s",
                    prop_id,
                    exc,
                )
                continue

            for res in reservations:
                raw_guest_id = res.get("guestID")
                if (str(raw_guest_id) if raw_guest_id is not None else "") == guest_id:
                    return PMSGuest(
                        guest_id=guest_id,
                        full_name=res.get("guestName") or "",
                        phone=res.get("guestPhone"),
                        email=res.get("guestEmail"),
                    )

        if properties_failed == len(properties) and last_error is not None:
            if isinstance(last_error, CloudbedsServiceError):
                raise self._translate_error(last_error) from last_error
            raise PMSProviderError(
                f"Unexpected error from Cloudbeds: {last_error}"
            ) from last_error

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

        Note: CloudbedsService.get_reservations() uses a default date window
            (24h ago to 365 days ahead). Reservations outside this window may
            not be found. This is a known limitation of the Cloudbeds API
            wrapper.
        """
        # TODO(optimization): Cloudbeds API does not support fetching a
        # single reservation by ID across properties.  This iterates all
        # properties which is expensive for multi-property accounts.
        # TODO: add bounded parallelism or reservation→property cache
        # to reduce O(num_properties) sequential round-trips.
        try:
            properties = await self._service.get_properties()
        except CloudbedsServiceError as exc:
            raise self._translate_error(exc) from exc
        except Exception as exc:
            raise PMSProviderError(f"Unexpected error from Cloudbeds: {exc}") from exc

        last_error: Exception | None = None
        properties_failed = 0

        for prop in properties:
            prop_id = prop.get("propertyID", "")
            if not prop_id:
                logger.warning("Skipping property with blank ID in get_custom_fields")
                continue
            try:
                reservations = await self._service.get_reservations(
                    property_id=prop_id,
                )
            except CloudbedsServiceError as exc:
                properties_failed += 1
                last_error = exc
                logger.warning(
                    "Failed to fetch reservations for property %s: %s",
                    prop_id,
                    exc,
                )
                continue
            except Exception as exc:
                properties_failed += 1
                last_error = exc
                logger.warning(
                    "Unexpected error fetching reservations for property %s: %s",
                    prop_id,
                    exc,
                )
                continue

            for res in reservations:
                raw_res_id = res.get("reservationID")
                if (
                    str(raw_res_id) if raw_res_id is not None else ""
                ) == reservation_id:
                    return dict(res.get("customFields") or {})

        if properties_failed == len(properties) and last_error is not None:
            if isinstance(last_error, CloudbedsServiceError):
                raise self._translate_error(last_error) from last_error
            raise PMSProviderError(
                f"Unexpected error from Cloudbeds: {last_error}"
            ) from last_error

        return {}

    async def refresh_token(
        self,
        credential: "OAuthCredential",
    ) -> TokenResult:
        """Refresh the OAuth token.

        Cloudbeds uses a separate OAuth service flow for token
        management.  This method is a no-op placeholder in the
        provider layer; direct token refresh is handled by
        ``OAuthService`` outside the provider abstraction.

        Args:
            credential: Current credential record (unused; kept
                for interface compatibility).

        Raises:
            NotImplementedError: Always — direct refresh is
                not supported for Cloudbeds providers.
        """
        raise NotImplementedError(
            "Cloudbeds token refresh is managed by OAuthService, not the provider layer"
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
        except Exception as exc:
            raise PMSProviderError(f"Unexpected error from Cloudbeds: {exc}") from exc
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
                    if not isinstance(r, dict):
                        continue
                    rid = (
                        r.get("roomID")
                        if r.get("roomID") is not None
                        else r.get("roomId")
                    )
                    if rid is not None:
                        room_ids.append(str(rid))
        if not room_ids:
            # Fallback: check top-level room key (both casing variants)
            top_rid = raw.get("roomID")
            if top_rid is None:
                top_rid = raw.get("roomId")
            if top_rid is not None:
                room_ids.append(str(top_rid))

        check_in = raw.get("startDate") or raw.get("checkInDate", "")
        check_out = raw.get("endDate") or raw.get("checkOutDate", "")

        raw_res_id = raw.get("reservationID")
        pms_booking_id = str(raw_res_id) if raw_res_id is not None else ""
        if not pms_booking_id:
            raise PMSProviderError(
                "Cloudbeds reservation missing required reservationID"
            )

        return PMSReservation(
            pms_booking_id=pms_booking_id,
            listing_pms_id=listing_pms_id,
            guest_name=raw.get("guestName"),
            guest_id=str(raw["guestID"]) if raw.get("guestID") is not None else None,
            check_in=_parse_date(check_in),
            check_out=_parse_date(check_out),
            status=(raw.get("status") or "confirmed").lower(),
            room_ids=tuple(room_ids),
            custom_data=dict(raw.get("customFields") or {}),
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
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)

    try:
        dt = datetime.fromisoformat(value)
    except (ValueError, TypeError):
        try:
            dt = datetime.strptime(value, "%Y-%m-%d")
        except (ValueError, TypeError) as exc:
            msg = f"Cannot parse date value: {value!r}"
            raise PMSProviderError(msg) from exc
    return dt.astimezone(UTC) if dt.tzinfo else dt.replace(tzinfo=UTC)
