# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the Cloudbeds provider implementation."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from src.models.oauth_credential import OAuthCredential
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
)
from src.providers.cloudbeds.provider import CloudbedsProvider, _parse_date
from src.services.cloudbeds_service import CloudbedsServiceError, RateLimitError

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_PROPERTIES = [
    {
        "propertyID": "P1",
        "propertyName": "Beach House",
        "propertyTimezone": "America/New_York",
    },
    {
        "propertyID": "P2",
        "propertyName": "Mountain Lodge",
        "propertyTimezone": "US/Mountain",
    },
]

SAMPLE_ROOMS = [
    {"roomID": "R1", "roomName": "Room 101", "roomTypeName": "Standard"},
    {"roomID": "R2", "roomName": "Room 102", "roomTypeName": "Deluxe"},
]

SAMPLE_RESERVATIONS = [
    {
        "reservationID": "RES1",
        "guestName": "Alice Smith",
        "guestID": "G1",
        "guestPhone": "+15551234567",
        "guestEmail": "alice@example.com",
        "startDate": "2026-03-01",
        "endDate": "2026-03-05",
        "status": "confirmed",
        "roomID": "R1",
        "customFields": {"early_check_in": "yes"},
    },
    {
        "reservationID": "RES2",
        "guestName": "Bob Jones",
        "guestID": "G2",
        "startDate": "2026-04-10",
        "endDate": "2026-04-15",
        "status": "checked_in",
        "rooms": [{"roomID": "R1"}, {"roomID": "R2"}],
        "customFields": {},
    },
]


@pytest.fixture
def provider():
    """Create a CloudbedsProvider instance with a test token."""
    return CloudbedsProvider(access_token="test_token")


@pytest.fixture
def mock_service(provider):
    """Mock the Cloudbeds service methods with sample data."""
    svc = provider._service
    svc.get_properties = AsyncMock(return_value=SAMPLE_PROPERTIES)
    svc.get_reservations = AsyncMock(return_value=SAMPLE_RESERVATIONS)
    svc.get_rooms = AsyncMock(return_value=SAMPLE_ROOMS)
    svc.refresh_access_token = AsyncMock(
        return_value=("new_tok", "new_ref", datetime(2026, 12, 31, tzinfo=UTC))
    )
    return svc


# ---------------------------------------------------------------------------
# Basic provider contract
# ---------------------------------------------------------------------------


class TestProviderType:
    """Tests for CloudbedsProvider type and identity."""

    def test_is_pms_provider(self, provider):
        """Test that CloudbedsProvider is a PMSProvider instance."""
        assert isinstance(provider, PMSProvider)

    def test_provider_type_is_cloudbeds(self, provider):
        """Test that provider_type returns 'cloudbeds'."""
        assert provider.provider_type == "cloudbeds"


# ---------------------------------------------------------------------------
# get_listings
# ---------------------------------------------------------------------------


class TestGetListings:
    """Tests for CloudbedsProvider.get_listings."""

    @pytest.mark.asyncio
    async def test_maps_properties_to_pms_listings(self, provider, mock_service):
        """Test that Cloudbeds properties are mapped to PMSListing objects."""
        listings = await provider.get_listings()

        assert len(listings) == 2
        assert all(isinstance(item, PMSListing) for item in listings)
        assert listings[0].pms_id == "P1"
        assert listings[0].name == "Beach House"
        assert listings[0].timezone == "America/New_York"
        assert listings[0].rooms == ()

    @pytest.mark.asyncio
    async def test_service_error_raises_pms_error(self, provider, mock_service):
        """Test that CloudbedsServiceError is translated to PMSProviderError."""
        mock_service.get_properties = AsyncMock(
            side_effect=CloudbedsServiceError("boom")
        )
        with pytest.raises(PMSProviderError):
            await provider.get_listings()

    @pytest.mark.asyncio
    async def test_rate_limit_error_translated(self, provider, mock_service):
        """Test that RateLimitError is translated to PMSRateLimitError."""
        mock_service.get_properties = AsyncMock(
            side_effect=RateLimitError("slow", retry_after=10.0)
        )
        with pytest.raises(PMSRateLimitError) as exc_info:
            await provider.get_listings()
        assert exc_info.value.retry_after == 10.0


# ---------------------------------------------------------------------------
# get_rooms
# ---------------------------------------------------------------------------


class TestGetRooms:
    """Tests for CloudbedsProvider.get_rooms."""

    @pytest.mark.asyncio
    async def test_maps_rooms_to_pms_rooms(self, provider, mock_service):
        """Test that Cloudbeds rooms are mapped to PMSRoom objects."""
        rooms = await provider.get_rooms("P1")

        assert len(rooms) == 2
        assert all(isinstance(r, PMSRoom) for r in rooms)
        assert rooms[0].pms_room_id == "R1"
        assert rooms[0].name == "Room 101"
        assert rooms[0].room_type == "Standard"

    @pytest.mark.asyncio
    async def test_service_error_raises_pms_error(self, provider, mock_service):
        """Test that service errors in get_rooms raise PMSProviderError."""
        mock_service.get_rooms = AsyncMock(side_effect=CloudbedsServiceError("fail"))
        with pytest.raises(PMSProviderError):
            await provider.get_rooms("P1")


# ---------------------------------------------------------------------------
# get_reservations
# ---------------------------------------------------------------------------


class TestGetReservations:
    """Tests for CloudbedsProvider.get_reservations."""

    @pytest.mark.asyncio
    async def test_maps_reservations(self, provider, mock_service):
        """Test that Cloudbeds reservations are mapped to PMSReservation objects."""
        reservations = await provider.get_reservations("P1")

        assert len(reservations) == 2
        assert all(isinstance(r, PMSReservation) for r in reservations)

        first = reservations[0]
        assert first.pms_booking_id == "RES1"
        assert first.listing_pms_id == "P1"
        assert first.guest_name == "Alice Smith"
        assert first.guest_id == "G1"
        assert first.status == "confirmed"
        assert first.custom_data == {"early_check_in": "yes"}
        assert first.check_in == datetime(2026, 3, 1, tzinfo=UTC)
        assert first.check_out == datetime(2026, 3, 5, tzinfo=UTC)

    @pytest.mark.asyncio
    async def test_single_room_id_from_top_level(self, provider, mock_service):
        """Test that a single top-level roomID is extracted correctly."""
        reservations = await provider.get_reservations("P1")
        assert reservations[0].room_ids == ("R1",)

    @pytest.mark.asyncio
    async def test_multiple_room_ids_from_rooms_list(self, provider, mock_service):
        """Test that multiple room IDs are extracted from the rooms list."""
        reservations = await provider.get_reservations("P1")
        assert reservations[1].room_ids == ("R1", "R2")

    @pytest.mark.asyncio
    async def test_passes_date_filters(self, provider, mock_service):
        """Test that start_date and end_date are forwarded to the service."""
        start = datetime(2026, 1, 1, tzinfo=UTC)
        end = datetime(2026, 12, 31, tzinfo=UTC)
        await provider.get_reservations("P1", start_date=start, end_date=end)

        mock_service.get_reservations.assert_called_once_with(
            property_id="P1", start_date=start, end_date=end
        )

    @pytest.mark.asyncio
    async def test_service_error_raises_pms_error(self, provider, mock_service):
        """Test that service errors in get_reservations raise PMSProviderError."""
        mock_service.get_reservations = AsyncMock(
            side_effect=CloudbedsServiceError("fail")
        )
        with pytest.raises(PMSProviderError):
            await provider.get_reservations("P1")


# ---------------------------------------------------------------------------
# get_guest
# ---------------------------------------------------------------------------


class TestGetGuest:
    """Tests for CloudbedsProvider.get_guest."""

    @pytest.mark.asyncio
    async def test_finds_guest_from_reservations(self, provider, mock_service):
        """Test that a guest is found by scanning all reservations."""
        guest = await provider.get_guest("G1")

        assert guest is not None
        assert isinstance(guest, PMSGuest)
        assert guest.guest_id == "G1"
        assert guest.full_name == "Alice Smith"
        assert guest.phone == "+15551234567"
        assert guest.email == "alice@example.com"

    @pytest.mark.asyncio
    async def test_returns_none_when_not_found(self, provider, mock_service):
        """Test that get_guest returns None for an unknown guest ID."""
        guest = await provider.get_guest("UNKNOWN")
        assert guest is None

    @pytest.mark.asyncio
    async def test_service_error_on_properties_raises(self, provider, mock_service):
        """Test that service errors during guest lookup raise PMSProviderError."""
        mock_service.get_properties = AsyncMock(
            side_effect=CloudbedsServiceError("fail")
        )
        with pytest.raises(PMSProviderError):
            await provider.get_guest("G1")

    @pytest.mark.asyncio
    async def test_all_properties_fail_raises(self, provider, mock_service):
        """Test that PMSProviderError is raised when all properties fail."""
        mock_service.get_reservations = AsyncMock(
            side_effect=CloudbedsServiceError("down")
        )
        with pytest.raises(PMSProviderError, match="down"):
            await provider.get_guest("G1")

    @pytest.mark.asyncio
    async def test_partial_property_failure_continues(self, provider, mock_service):
        """Test that guest is found even if some properties fail."""
        calls = [0]

        async def _side_effect(**kwargs):
            """Simulate first property failing, second succeeding."""
            calls[0] += 1
            if calls[0] == 1:
                raise CloudbedsServiceError("property P1 down")
            return SAMPLE_RESERVATIONS

        mock_service.get_reservations = AsyncMock(side_effect=_side_effect)
        guest = await provider.get_guest("G1")
        assert guest is not None
        assert guest.guest_id == "G1"


# ---------------------------------------------------------------------------
# get_custom_fields
# ---------------------------------------------------------------------------


class TestGetCustomFields:
    """Tests for CloudbedsProvider.get_custom_fields."""

    @pytest.mark.asyncio
    async def test_returns_custom_data(self, provider, mock_service):
        """Test that custom fields are returned for a known reservation."""
        fields = await provider.get_custom_fields("RES1")
        assert fields == {"early_check_in": "yes"}

    @pytest.mark.asyncio
    async def test_returns_empty_when_not_found(self, provider, mock_service):
        """Test that an empty dict is returned for an unknown reservation."""
        fields = await provider.get_custom_fields("MISSING")
        assert fields == {}

    @pytest.mark.asyncio
    async def test_all_properties_fail_raises(self, provider, mock_service):
        """Test that PMSProviderError is raised when all properties fail."""
        mock_service.get_reservations = AsyncMock(
            side_effect=CloudbedsServiceError("down")
        )
        with pytest.raises(PMSProviderError, match="down"):
            await provider.get_custom_fields("RES1")


# ---------------------------------------------------------------------------
# refresh_token
# ---------------------------------------------------------------------------


class TestRefreshToken:
    """Tests for CloudbedsProvider.refresh_token."""

    @pytest.mark.asyncio
    async def test_raises_not_implemented(self, provider, mock_service):
        """Test that refresh_token raises NotImplementedError."""
        mock_credential = MagicMock(spec=OAuthCredential)
        with pytest.raises(NotImplementedError, match="OAuthService"):
            await provider.refresh_token(credential=mock_credential)


# ---------------------------------------------------------------------------
# test_connection
# ---------------------------------------------------------------------------


class TestTestConnection:
    """Tests for CloudbedsProvider.test_connection."""

    @pytest.mark.asyncio
    async def test_returns_true_on_success(self, provider, mock_service):
        """Test that test_connection returns True when service succeeds."""
        assert await provider.test_connection() is True

    @pytest.mark.asyncio
    async def test_raises_on_failure(self, provider, mock_service):
        """Test that test_connection raises PMSConnectionError on failure."""
        mock_service.get_properties = AsyncMock(
            side_effect=CloudbedsServiceError("connection refused")
        )
        with pytest.raises(PMSConnectionError):
            await provider.test_connection()


# ---------------------------------------------------------------------------
# Error translation
# ---------------------------------------------------------------------------


class TestErrorTranslation:
    """Tests for CloudbedsProvider._translate_error."""

    def test_rate_limit_translated(self):
        """Test that RateLimitError maps to PMSRateLimitError."""
        err = RateLimitError("slow", retry_after=5.0)
        result = CloudbedsProvider._translate_error(err)
        assert isinstance(result, PMSRateLimitError)
        assert result.retry_after == 5.0

    def test_generic_service_error_translated(self):
        """Test that generic CloudbedsServiceError maps to PMSProviderError."""
        err = CloudbedsServiceError("something broke")
        result = CloudbedsProvider._translate_error(err)
        assert isinstance(result, PMSProviderError)

    def test_auth_keyword_translated(self):
        """Test that auth-related error messages map to PMSAuthenticationError."""
        err = CloudbedsServiceError("401 Unauthorized")
        result = CloudbedsProvider._translate_error(err)

        assert isinstance(result, PMSAuthenticationError)

    def test_connection_keyword_translated(self):
        """Test that connection-related error messages map to PMSConnectionError."""
        err = CloudbedsServiceError("connection timeout")
        result = CloudbedsProvider._translate_error(err)
        assert isinstance(result, PMSConnectionError)


# ---------------------------------------------------------------------------
# _parse_date
# ---------------------------------------------------------------------------


class TestParseDate:
    """Tests for the _parse_date helper function."""

    def test_parses_iso_date_string(self):
        """Test that ISO date strings are parsed correctly."""
        result = _parse_date("2026-03-01")
        assert result == datetime(2026, 3, 1, tzinfo=UTC)

    def test_parses_iso_datetime_string(self):
        """Test that ISO datetime strings are parsed correctly."""
        result = _parse_date("2026-03-01T10:30:00")
        assert result == datetime(2026, 3, 1, 10, 30, tzinfo=UTC)

    def test_passes_through_aware_datetime(self):
        """Test that timezone-aware datetimes pass through unchanged."""
        dt = datetime(2026, 3, 1, tzinfo=UTC)
        result = _parse_date(dt)
        assert result == dt
        assert result.tzinfo is UTC

    def test_adds_utc_to_naive_datetime(self):
        """Test that naive datetimes get UTC timezone added."""
        dt = datetime(2026, 3, 1)
        result = _parse_date(dt)
        assert result.tzinfo is UTC

    def test_none_raises_provider_error(self):
        """Test that None raises PMSProviderError."""
        with pytest.raises(PMSProviderError, match="empty or None"):
            _parse_date(None)

    def test_empty_string_raises_provider_error(self):
        """Test that empty string raises PMSProviderError."""
        with pytest.raises(PMSProviderError, match="empty or None"):
            _parse_date("")

    def test_invalid_string_raises_provider_error(self):
        """Test that unparseable strings raise PMSProviderError."""
        with pytest.raises(PMSProviderError, match="Cannot parse date"):
            _parse_date("not-a-date")
