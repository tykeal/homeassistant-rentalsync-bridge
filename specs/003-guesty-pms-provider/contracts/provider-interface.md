<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->
# Provider Interface Contract: PMS Provider Abstraction

**Feature Branch**: `003-guesty-pms-provider`
**Date**: 2025-07-15

## Overview

This contract defines the abstract interface that all PMS providers must implement.
The interface uses Python ABC with frozen dataclass DTOs for the provider-to-sync
service boundary.

## PMSProvider ABC

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.models.oauth_credential import OAuthCredential


@dataclass(frozen=True)
class PMSListing:
    """Normalized listing from any PMS provider."""

    pms_id: str
    name: str
    timezone: str
    address: str | None = None
    rooms: list["PMSRoom"] = field(default_factory=list)


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
    """

    pms_booking_id: str
    listing_pms_id: str
    guest_name: str | None
    guest_id: str | None
    check_in: datetime
    check_out: datetime
    status: str  # "confirmed" | "checked_in" | "checked_out" | "cancelled"
    room_ids: list[str]
    custom_data: dict[str, Any]


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


class PMSProvider(ABC):
    """Abstract base class for PMS provider implementations.

    Each PMS provider (Cloudbeds, Guesty, etc.) must implement all abstract
    methods. Implementations handle API communication, pagination, rate
    limiting, and data normalization into the DTO types above.
    """

    @abstractmethod
    async def get_listings(self) -> list[PMSListing]:
        """Fetch all listings/properties from the PMS.

        Returns:
            List of normalized listings with embedded room information.

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
            start_date: Filter start (inclusive). Default: 24h ago.
            end_date: Filter end (inclusive). Default: 365 days future.

        Returns:
            List of normalized reservations with guest names resolved.

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
            List of normalized rooms. For single-unit listings,
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
            PMSProviderError: If API communication fails (except 404).
        """
        ...

    @abstractmethod
    async def get_custom_fields(
        self, reservation_id: str
    ) -> dict[str, Any]:
        """Fetch custom field values for a reservation.

        Args:
            reservation_id: PMS-specific reservation identifier.

        Returns:
            Dictionary of field_id -> value mappings.

        Raises:
            PMSProviderError: If API communication fails.
        """
        ...

    @abstractmethod
    async def refresh_token(
        self, credential: "OAuthCredential"
    ) -> TokenResult:
        """Refresh or acquire a new access token.

        For client_credentials providers (Guesty): acquires new token.
        For authorization_code providers (Cloudbeds): refreshes existing token.

        Args:
            credential: Current credential record with client_id/secret.

        Returns:
            New token information.

        Raises:
            PMSProviderError: If token operation fails.
            TokenRateLimitError: If provider-specific token rate limit exceeded.
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
```

## Exception Hierarchy

```python
class PMSProviderError(Exception):
    """Base exception for all PMS provider errors."""
    pass


class PMSAuthenticationError(PMSProviderError):
    """Authentication failed (invalid credentials, expired token)."""
    pass


class PMSRateLimitError(PMSProviderError):
    """API rate limit exceeded."""

    def __init__(
        self, message: str, retry_after: float | None = None
    ) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class TokenRateLimitError(PMSProviderError):
    """Token request rate limit exceeded (Guesty: 5/day)."""

    def __init__(self, message: str, reset_at: datetime | None = None) -> None:
        super().__init__(message)
        self.reset_at = reset_at


class PMSConnectionError(PMSProviderError):
    """Network or connection error communicating with PMS API."""
    pass
```

## Provider Registry Contract

```python
def register_provider(pms_type: str, provider_class: type[PMSProvider]) -> None:
    """Register a PMS provider implementation.

    Args:
        pms_type: Provider identifier string (e.g., "cloudbeds", "guesty").
        provider_class: Class implementing PMSProvider ABC.

    Raises:
        ValueError: If pms_type already registered.
    """
    ...


def get_provider_class(pms_type: str) -> type[PMSProvider]:
    """Get the provider class for a given type.

    Args:
        pms_type: Provider identifier string.

    Returns:
        Provider class.

    Raises:
        ValueError: If pms_type not registered.
    """
    ...


def create_provider(
    pms_type: str,
    access_token: str | None = None,
    refresh_token: str | None = None,
    api_key: str | None = None,
    client_id: str | None = None,
    client_secret: str | None = None,
) -> PMSProvider:
    """Create and return a configured provider instance.

    Args:
        pms_type: Provider identifier string.
        access_token: OAuth access token.
        refresh_token: OAuth refresh token (Cloudbeds).
        api_key: API key (Cloudbeds).
        client_id: Client ID (Guesty token acquisition).
        client_secret: Client secret (Guesty token acquisition).

    Returns:
        Configured provider instance.

    Raises:
        ValueError: If pms_type not registered.
    """
    ...


def list_providers() -> list[dict[str, Any]]:
    """List all registered providers with their metadata.

    Returns:
        List of provider info dicts for the /api/providers endpoint.
    """
    ...
```

## Implementation Requirements

### CloudbedsProvider

Must wrap the existing `CloudbedsService` and:
- Map `get_properties()` → `get_listings()` returning `PMSListing` objects
- Map `get_reservations()` → extract guest name inline (already in response)
- Map `get_rooms()` → `PMSRoom` objects
- `get_guest()` → return guest data extracted from reservation (Cloudbeds embeds it)
- `get_custom_fields()` → return custom_data from reservation response
- `refresh_token()` → delegate to existing `OAuthService.refresh_token()`
- `test_connection()` → call `get_properties()` and check for success
- `provider_type` → `"cloudbeds"`

### GuestyProvider

Must implement against the Guesty Open API, primarily using v1 endpoints (`/v1/listings`, `/v1/reservations`, `/v1/guests/{id}`) with the v3 custom-fields endpoint (`/v1/reservations-v3/{id}/custom-fields`), and:
- `get_listings()` → paginated `GET /v1/listings`, handle multi-unit → Room mapping
- `get_reservations()` → paginated `GET /v1/reservations` with guest ID resolution
- `get_rooms()` → extract from listing detail (multi-unit) or create implicit (single-unit)
- `get_guest()` → `GET /v1/guests/{id}`, return None on 404
- `get_custom_fields()` → `GET /v1/reservations-v3/{id}/custom-fields`
- `refresh_token()` → `POST /oauth2/token` with rate tracking
- `test_connection()` → validate authentication and API reachability via a successful authenticated request (e.g., call `get_listings()` and verify the request succeeds); do not require at least one listing for success — surface zero listings as a separate warning
- `provider_type` → `"guesty"`
- All API calls use exponential backoff on 429 responses
- Pagination continues until `len(results) < limit`
