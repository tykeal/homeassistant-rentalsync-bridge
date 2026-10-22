# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""PMS Provider abstraction layer — base classes, DTOs, and exceptions."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.models.oauth_credential import OAuthCredential


# Provider statuses we understand but deliberately never export.
#
# Each one means the stay is not happening: ``expired`` (an unpaid hold that
# lapsed), ``closed`` (a reservation closed out without a stay), ``declined``
# (a request the host turned down) and ``awaiting_payment`` (a booking that
# has not been paid for yet).
#
# These are withheld, not retired: the sync stores them verbatim so the
# reservation stays "seen" (the prune loop must not cancel it), while
# ``BookingRepository.get_confirmed_for_listing`` keeps it out of the iCal
# export.  Because they are expected on healthy accounts, every layer logs
# them at DEBUG; only a status outside this set is WARNING-worthy evidence
# that a provider's status mapping has drifted.
#
# This is the shared source of truth: providers and the sync import it here
# rather than keeping their own copies.
RECOGNISED_NON_EXPORT_STATUSES = frozenset(
    {"expired", "closed", "declined", "awaiting_payment"}
)


# ---------------------------------------------------------------------------
# Data Transfer Objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PMSListing:
    """Normalized listing from any PMS provider."""

    pms_id: str
    name: str
    timezone: str
    address: str | None = None
    rooms: tuple["PMSRoom", ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class PMSRoom:
    """Normalized room/unit from any PMS provider."""

    pms_room_id: str
    name: str
    room_type: str | None = None


@dataclass(frozen=True)
class PMSReservation:
    """Normalized reservation from any PMS provider.

    All provider-specific data formats are converted to Python native types
    before constructing this DTO.

    Note:
        ``custom_data`` is a plain ``dict`` and only attribute reassignment
        is prevented by ``frozen=True``.  The dict contents are still mutable
        (shallow freeze).  Treat ``custom_data`` as read-only after construction.
    """

    pms_booking_id: str
    listing_pms_id: str
    guest_name: str | None
    guest_id: str | None
    check_in: datetime
    check_out: datetime
    status: str  # one of src.services.sync_service.KNOWN_BOOKING_STATUSES, or
    # a raw provider value we do not recognise (withheld from export)
    room_ids: tuple[str, ...]
    custom_data: dict[str, Any]
    # Provider-reported "last modified" instant for the reservation, when the
    # provider exposes one.  Used by the sync as a change-detection signal so
    # unchanged reservations can skip redundant enrichment calls.  Providers
    # without such a field leave this None, which disables the optimisation
    # for them (never skip) rather than guessing that nothing changed.
    updated_at: datetime | None = None


@dataclass(frozen=True)
class PMSGuest:
    """Normalized guest from any PMS provider."""

    guest_id: str
    full_name: str
    phone: str | None = None
    email: str | None = None


@dataclass(frozen=True)
class TokenResult:
    """Result of a token refresh/acquire operation."""

    access_token: str
    refresh_token: str | None
    expires_at: datetime


# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------


class PMSProviderError(Exception):
    """Base exception for all PMS provider errors."""


class PMSAuthenticationError(PMSProviderError):
    """Authentication failed (invalid credentials, expired token)."""


class PMSRateLimitError(PMSProviderError):
    """API rate limit exceeded."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        """Initialize PMSRateLimitError.

        Args:
            message: Error description.
            retry_after: Seconds to wait before retrying.
        """
        super().__init__(message)
        self.retry_after = retry_after


class TokenRateLimitError(PMSProviderError):
    """Token request rate limit exceeded (Guesty: 5/day)."""

    def __init__(self, message: str, reset_at: datetime | None = None) -> None:
        """Initialize TokenRateLimitError.

        Args:
            message: Error description.
            reset_at: When the rate limit resets.
        """
        super().__init__(message)
        self.reset_at = reset_at


class PMSConnectionError(PMSProviderError):
    """Network or connection error communicating with PMS API."""


# ---------------------------------------------------------------------------
# Abstract base class
# ---------------------------------------------------------------------------


class PMSProvider(ABC):
    """Abstract base class for PMS provider implementations.

    Each PMS provider (Cloudbeds, Guesty, etc.) must implement all abstract
    methods.  Implementations handle API communication, pagination, rate
    limiting, and data normalization into the DTO types above.
    """

    @abstractmethod
    async def get_listings(self) -> list[PMSListing]:
        """Fetch all listings/properties from the PMS.

        Returns:
            List of normalized listings.  ``rooms`` may be empty;
            use ``get_rooms()`` to fetch room details per listing.

        Raises:
            PMSProviderError: If API communication fails.
        """
        ...

    @abstractmethod
    async def get_reservations(
        self,
        listing_pms_id: str,
        *,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> list[PMSReservation]:
        """Fetch reservations for a specific listing.

        Args:
            listing_pms_id: PMS-specific listing identifier.
            start_date: Filter start (inclusive).  Default: 24 h ago.
            end_date: Filter end (inclusive).  Default: 365 days future.

        Returns:
            List of normalized reservations.  ``guest_name`` may be
            ``None`` if the provider does not include guest details
            in the reservation payload; callers can use ``guest_id``
            with ``get_guest()`` to resolve names when needed.

        Raises:
            PMSProviderError: If API communication fails.
        """
        ...

    @abstractmethod
    async def get_rooms(self, listing_pms_id: str) -> list[PMSRoom]:
        """Fetch rooms/units for a specific listing.

        Args:
            listing_pms_id: PMS-specific listing identifier.

        Returns:
            List of normalized rooms.  For single-unit listings,
            returns a list with one implicit room.

        Raises:
            PMSProviderError: If API communication fails.
        """
        ...

    @abstractmethod
    async def get_guest(self, guest_id: str) -> PMSGuest | None:
        """Fetch guest details by guest identifier.

        Args:
            guest_id: PMS-specific guest identifier.

        Returns:
            Normalized guest data, or None if guest not found.

        Raises:
            PMSProviderError: If all underlying lookups fail.
        """
        ...

    @abstractmethod
    async def get_custom_fields(self, reservation_id: str) -> dict[str, Any]:
        """Fetch custom field values for a reservation.

        Args:
            reservation_id: PMS-specific reservation identifier.

        Returns:
            Dictionary of field_id → value mappings.

        Raises:
            PMSProviderError: If all underlying lookups fail.
        """
        ...

    async def get_custom_field_definitions(self) -> dict[str, str]:
        """Fetch human-readable labels for custom field identifiers.

        Providers whose custom-field values are keyed by an opaque
        identifier (Guesty's ObjectIds) override this to resolve those
        identifiers to the labels their users configured.

        Implementations MUST NOT raise: a missing permission, an API
        failure or a malformed response has to degrade to an empty
        mapping, because a sync must never fail over cosmetic labels.

        Returns:
            Mapping of field key to display label; empty when unknown.
        """
        return {}

    @abstractmethod
    async def refresh_token(self, credential: "OAuthCredential") -> TokenResult:
        """Refresh the OAuth token for providers that support direct refresh.

        Providers that manage tokens through an external mechanism
        (e.g., a separate OAuth service) should raise NotImplementedError.

        Args:
            credential: The current OAuth credential to refresh.

        Returns:
            TokenResult with new access token and expiry.

        Raises:
            NotImplementedError: If the provider does not support direct refresh.
            PMSAuthenticationError: If refresh fails due to invalid credentials.
        """
        ...

    @abstractmethod
    async def test_connection(self) -> bool:
        """Test that the provider connection is working.

        Makes a lightweight API call to verify credentials are valid
        and the API is reachable.

        Returns:
            True if connection is healthy.

        Raises:
            PMSProviderError: If connection test fails.
        """
        ...

    @property
    @abstractmethod
    def provider_type(self) -> str:
        """Return the provider type identifier string.

        Returns:
            Provider type (e.g., "cloudbeds", "guesty").
        """
        ...

    @property
    def has_separate_custom_fields(self) -> bool:
        """Whether custom fields require a separate API call.

        Providers that expose custom fields via a dedicated endpoint
        (e.g. Guesty v3) should return ``True``.  Providers that
        embed custom fields directly in reservation payloads (e.g.
        Cloudbeds) should leave this as ``False`` to avoid redundant
        and expensive API calls during enrichment.
        """
        return False
