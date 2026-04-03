# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for GuestyProvider."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
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
from src.providers.guesty.auth import GuestyTokenManager
from src.providers.guesty.service import (
    GUESTY_BASE_URL,
    GuestyProvider,
    _format_address,
    _parse_date,
    _parse_retry_after,
)

# ---------------------------------------------------------------------------
# Sample API responses
# ---------------------------------------------------------------------------

SAMPLE_LISTINGS_PAGE1 = {
    "results": [
        {
            "_id": "L1",
            "title": "Beach House",
            "timezone": "America/New_York",
            "address": {"full": "123 Beach Rd, Miami, FL"},
        },
        {
            "_id": "L2",
            "nickname": "Mountain Lodge",
            "timezone": "US/Mountain",
            "address": None,
        },
    ]
}

SAMPLE_LISTINGS_PAGE2 = {
    "results": [
        {
            "_id": "L3",
            "title": "City Apt",
            "timezone": "UTC",
            "address": {"street": "456 Main St", "city": "NYC"},
        },
    ]
}

SAMPLE_RESERVATIONS = {
    "results": [
        {
            "_id": "R1",
            "status": "confirmed",
            "guest": {"_id": "G1", "fullName": "Alice Smith"},
            "checkIn": "2026-03-01T14:00:00Z",
            "checkOut": "2026-03-05T11:00:00Z",
            "listingId": "L1",
        },
        {
            "_id": "R2",
            "status": "checked_in",
            "guest": {"_id": "G2", "fullName": "Bob Jones"},
            "checkIn": "2026-04-10",
            "checkOut": "2026-04-15",
            "listingId": "L1",
        },
        {
            "_id": "R3",
            "status": "inquiry",
            "guest": {"_id": "G3", "fullName": "Excluded"},
            "checkIn": "2026-05-01",
            "checkOut": "2026-05-05",
            "listingId": "L1",
        },
        {
            "_id": "R4",
            "status": "canceled",
            "guest": {"_id": "G4", "fullName": "Cancelled Guest"},
            "checkIn": "2026-06-01",
            "checkOut": "2026-06-05",
            "listingId": "L1",
        },
    ]
}

SAMPLE_LISTING_MULTI_UNIT = {
    "_id": "L1",
    "title": "Beach House",
    "childListings": [
        {"_id": "C1", "title": "Suite A", "roomType": "suite"},
        {"_id": "C2", "nickname": "Room B", "roomType": "standard"},
    ],
}

SAMPLE_LISTING_SINGLE_UNIT = {
    "_id": "L1",
    "title": "Beach House",
    "propertyType": "apartment",
    "childListings": [],
}

SAMPLE_GUEST = {
    "_id": "G1",
    "fullName": "Alice Smith",
    "phone": "+15551234567",
    "email": "alice@example.com",
}

SAMPLE_CUSTOM_FIELDS = [
    {"fieldId": "cf_1", "value": "early_checkin"},
    {"fieldId": "cf_2", "value": "ground_floor"},
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_token_manager():
    """Create a mock GuestyTokenManager with test defaults."""
    mgr = AsyncMock(spec=GuestyTokenManager)
    mgr.get_token = AsyncMock(return_value="test-token")
    mgr.invalidate_cache = MagicMock()
    mgr.cached_expires_at = datetime.now(UTC) + timedelta(hours=24)
    return mgr


@pytest.fixture
def mock_http_client():
    """Create a mock httpx.AsyncClient."""
    return AsyncMock(spec=httpx.AsyncClient)


@pytest.fixture
def guesty_provider(mock_token_manager, mock_http_client):
    """Create a GuestyProvider with mocked dependencies."""
    return GuestyProvider(
        token_manager=mock_token_manager,
        http_client=mock_http_client,
    )


def _make_response(json_data, status_code=200, headers=None):
    """Build a mock httpx.Response from JSON data."""
    return httpx.Response(
        status_code=status_code,
        json=json_data,
        headers=headers or {},
        request=httpx.Request("GET", f"{GUESTY_BASE_URL}/test"),
    )


# ---------------------------------------------------------------------------
# Basic provider contract
# ---------------------------------------------------------------------------


class TestProviderType:
    """Tests for GuestyProvider type and identity."""

    def test_is_pms_provider(self, guesty_provider):
        """Test that GuestyProvider is a PMSProvider."""
        assert isinstance(guesty_provider, PMSProvider)

    def test_provider_type_is_guesty(self, guesty_provider):
        """Test that provider_type returns guesty."""
        assert guesty_provider.provider_type == "guesty"


# ---------------------------------------------------------------------------
# get_listings
# ---------------------------------------------------------------------------


class TestGetListings:
    """Tests for GuestyProvider.get_listings."""

    @pytest.mark.asyncio
    async def test_maps_listings(self, guesty_provider, mock_http_client):
        """Test that API listings map to PMSListing DTOs."""
        mock_http_client.request = AsyncMock(
            return_value=_make_response(SAMPLE_LISTINGS_PAGE1)
        )

        listings = await guesty_provider.get_listings()

        assert len(listings) == 2
        assert all(isinstance(item, PMSListing) for item in listings)
        assert listings[0].pms_id == "L1"
        assert listings[0].name == "Beach House"
        assert listings[0].timezone == "America/New_York"
        assert listings[0].address == "123 Beach Rd, Miami, FL"
        assert listings[1].pms_id == "L2"
        assert listings[1].name == "Mountain Lodge"

    @pytest.mark.asyncio
    async def test_pagination(self, guesty_provider, mock_http_client):
        """Test that multiple pages are fetched."""
        full_page = {
            "results": [
                {"_id": f"L{i}", "title": f"P{i}", "timezone": "UTC"}
                for i in range(100)
            ],
        }

        mock_http_client.request = AsyncMock(
            side_effect=[
                _make_response(full_page),
                _make_response(SAMPLE_LISTINGS_PAGE2),
            ]
        )

        listings = await guesty_provider.get_listings()

        assert len(listings) == 101
        assert mock_http_client.request.call_count == 2

    @pytest.mark.asyncio
    async def test_empty_results(self, guesty_provider, mock_http_client):
        """Test that empty results return an empty list."""
        mock_http_client.request = AsyncMock(
            return_value=_make_response({"results": []})
        )

        listings = await guesty_provider.get_listings()
        assert listings == []

    @pytest.mark.asyncio
    async def test_skips_listings_without_id(self, guesty_provider, mock_http_client):
        """Test that listings without _id are skipped."""
        mock_http_client.request = AsyncMock(
            return_value=_make_response(
                {
                    "results": [
                        {"_id": "", "title": "No ID"},
                        {"_id": "L1", "title": "Valid"},
                    ]
                }
            )
        )

        listings = await guesty_provider.get_listings()
        assert len(listings) == 1
        assert listings[0].pms_id == "L1"


# ---------------------------------------------------------------------------
# get_reservations
# ---------------------------------------------------------------------------


class TestGetReservations:
    """Tests for GuestyProvider.get_reservations."""

    @pytest.mark.asyncio
    async def test_maps_reservations(self, guesty_provider, mock_http_client):
        """Test that reservations map to PMSReservation DTOs."""
        mock_http_client.request = AsyncMock(
            return_value=_make_response(SAMPLE_RESERVATIONS)
        )

        reservations = await guesty_provider.get_reservations("L1")

        assert all(isinstance(r, PMSReservation) for r in reservations)
        confirmed = [r for r in reservations if r.pms_booking_id == "R1"]
        assert len(confirmed) == 1
        assert confirmed[0].status == "confirmed"
        assert confirmed[0].guest_name == "Alice Smith"
        assert confirmed[0].guest_id == "G1"

    @pytest.mark.asyncio
    async def test_status_mapping(self, guesty_provider, mock_http_client):
        """Test that Guesty statuses map to normalised statuses."""
        mock_http_client.request = AsyncMock(
            return_value=_make_response(SAMPLE_RESERVATIONS)
        )

        reservations = await guesty_provider.get_reservations("L1")
        statuses = {r.pms_booking_id: r.status for r in reservations}

        assert statuses["R1"] == "confirmed"
        assert statuses["R2"] == "confirmed"
        assert statuses["R4"] == "cancelled"

    @pytest.mark.asyncio
    async def test_excludes_inquiry_and_reserved(
        self,
        guesty_provider,
        mock_http_client,
    ):
        """Test that inquiry statuses are excluded."""
        mock_http_client.request = AsyncMock(
            return_value=_make_response(SAMPLE_RESERVATIONS)
        )

        reservations = await guesty_provider.get_reservations("L1")
        booking_ids = [r.pms_booking_id for r in reservations]

        assert "R3" not in booking_ids

    @pytest.mark.asyncio
    async def test_date_filters_passed(self, guesty_provider, mock_http_client):
        """Test that date filters are forwarded to the API."""
        mock_http_client.request = AsyncMock(
            return_value=_make_response({"results": []})
        )

        start = datetime(2026, 1, 1, tzinfo=UTC)
        end = datetime(2026, 12, 31, tzinfo=UTC)
        await guesty_provider.get_reservations("L1", start_date=start, end_date=end)

        call_kwargs = mock_http_client.request.call_args
        params = call_kwargs.kwargs.get("params") or call_kwargs[1].get("params", {})
        assert params["checkIn"] == "2026-01-01"
        assert params["checkOut"] == "2026-12-31"

    @pytest.mark.asyncio
    async def test_pagination(self, guesty_provider, mock_http_client):
        """Test that multiple pages are fetched."""
        full_page = {
            "results": [
                {
                    "_id": f"R{i}",
                    "status": "confirmed",
                    "guest": {"_id": f"G{i}", "fullName": f"Guest {i}"},
                    "checkIn": "2026-03-01",
                    "checkOut": "2026-03-05",
                    "listingId": "L1",
                }
                for i in range(100)
            ]
        }
        last_page = {
            "results": [
                {
                    "_id": "R100",
                    "status": "confirmed",
                    "guest": {"_id": "G100", "fullName": "Last Guest"},
                    "checkIn": "2026-03-01",
                    "checkOut": "2026-03-05",
                    "listingId": "L1",
                }
            ]
        }

        mock_http_client.request = AsyncMock(
            side_effect=[_make_response(full_page), _make_response(last_page)]
        )

        reservations = await guesty_provider.get_reservations("L1")
        assert len(reservations) == 101


# ---------------------------------------------------------------------------
# get_rooms
# ---------------------------------------------------------------------------


class TestGetRooms:
    """Tests for GuestyProvider.get_rooms."""

    @pytest.mark.asyncio
    async def test_multi_unit_listing(self, guesty_provider, mock_http_client):
        """Test that child listings map to PMSRoom DTOs."""
        mock_http_client.request = AsyncMock(
            return_value=_make_response(SAMPLE_LISTING_MULTI_UNIT)
        )

        rooms = await guesty_provider.get_rooms("L1")

        assert len(rooms) == 2
        assert all(isinstance(r, PMSRoom) for r in rooms)
        assert rooms[0].pms_room_id == "C1"
        assert rooms[0].name == "Suite A"
        assert rooms[0].room_type == "suite"
        assert rooms[1].pms_room_id == "C2"
        assert rooms[1].name == "Room B"

    @pytest.mark.asyncio
    async def test_single_unit_listing(self, guesty_provider, mock_http_client):
        """Test that single-unit creates one implicit room."""
        mock_http_client.request = AsyncMock(
            return_value=_make_response(SAMPLE_LISTING_SINGLE_UNIT)
        )

        rooms = await guesty_provider.get_rooms("L1")

        assert len(rooms) == 1
        assert rooms[0].pms_room_id == "L1"
        assert rooms[0].name == "Beach House"
        assert rooms[0].room_type == "apartment"

    @pytest.mark.asyncio
    async def test_no_child_listings_key(self, guesty_provider, mock_http_client):
        """Test fallback when childListings key is absent."""
        mock_http_client.request = AsyncMock(
            return_value=_make_response(
                {
                    "_id": "L5",
                    "title": "Simple Place",
                    "propertyType": "house",
                }
            )
        )

        rooms = await guesty_provider.get_rooms("L5")
        assert len(rooms) == 1
        assert rooms[0].pms_room_id == "L5"


# ---------------------------------------------------------------------------
# get_guest
# ---------------------------------------------------------------------------


class TestGetGuest:
    """Tests for GuestyProvider.get_guest."""

    @pytest.mark.asyncio
    async def test_returns_guest(self, guesty_provider, mock_http_client):
        """Test that guest data is returned as PMSGuest."""
        mock_http_client.request = AsyncMock(return_value=_make_response(SAMPLE_GUEST))

        guest = await guesty_provider.get_guest("G1")

        assert guest is not None
        assert isinstance(guest, PMSGuest)
        assert guest.guest_id == "G1"
        assert guest.full_name == "Alice Smith"
        assert guest.phone == "+15551234567"
        assert guest.email == "alice@example.com"

    @pytest.mark.asyncio
    async def test_returns_none_on_404(self, guesty_provider, mock_http_client):
        """Test that a 404 returns None."""
        mock_http_client.request = AsyncMock(
            return_value=httpx.Response(
                status_code=404,
                text="Not Found",
                request=httpx.Request("GET", f"{GUESTY_BASE_URL}/v1/guests/UNKNOWN"),
            )
        )

        guest = await guesty_provider.get_guest("UNKNOWN")
        assert guest is None

    @pytest.mark.asyncio
    async def test_raises_on_non_404_error(self, guesty_provider, mock_http_client):
        """Test that non-404 errors raise PMSProviderError."""
        mock_http_client.request = AsyncMock(
            return_value=httpx.Response(
                status_code=500,
                text="Server Error",
                request=httpx.Request("GET", f"{GUESTY_BASE_URL}/v1/guests/G1"),
            )
        )

        with pytest.raises(PMSProviderError):
            await guesty_provider.get_guest("G1")


# ---------------------------------------------------------------------------
# Custom fields (v3 endpoint)
# ---------------------------------------------------------------------------


class TestGetCustomFields:
    """Tests for GuestyProvider.get_custom_fields."""

    @pytest.mark.asyncio
    async def test_returns_field_map(self, guesty_provider, mock_http_client):
        """Test that custom fields are returned as a dict."""
        mock_http_client.request = AsyncMock(
            return_value=_make_response(SAMPLE_CUSTOM_FIELDS)
        )

        fields = await guesty_provider.get_custom_fields("R1")

        assert fields == {
            "cf_1": "early_checkin",
            "cf_2": "ground_floor",
        }

    @pytest.mark.asyncio
    async def test_empty_custom_fields(self, guesty_provider, mock_http_client):
        """Test that empty custom fields return empty dict."""
        mock_http_client.request = AsyncMock(return_value=_make_response([]))

        fields = await guesty_provider.get_custom_fields("R1")
        assert fields == {}

    @pytest.mark.asyncio
    async def test_dict_response_with_results(self, guesty_provider, mock_http_client):
        """Test fallback parsing for dict-wrapped results."""
        mock_http_client.request = AsyncMock(
            return_value=_make_response(
                {
                    "results": [
                        {"fieldId": "f1", "value": "v1"},
                    ]
                }
            )
        )

        fields = await guesty_provider.get_custom_fields("R1")
        assert fields == {"f1": "v1"}


# ---------------------------------------------------------------------------
# 429 retry logic
# ---------------------------------------------------------------------------


class TestRetryLogic:
    """Tests for HTTP 429 retry logic."""

    @pytest.mark.asyncio
    async def test_retries_on_429(self, guesty_provider, mock_http_client):
        """Test that 429 responses trigger retries."""
        rate_limit_resp = httpx.Response(
            status_code=429,
            text="Too Many Requests",
            headers={"Retry-After": "0.01"},
            request=httpx.Request("GET", f"{GUESTY_BASE_URL}/test"),
        )
        success_resp = _make_response({"results": []})

        mock_http_client.request = AsyncMock(
            side_effect=[rate_limit_resp, success_resp]
        )

        response = await guesty_provider._request("GET", "/test")
        assert response.status_code == 200
        assert mock_http_client.request.call_count == 2

    @pytest.mark.asyncio
    async def test_raises_after_max_retries(self, guesty_provider, mock_http_client):
        """Test that PMSRateLimitError is raised after max retries."""
        rate_limit_resp = httpx.Response(
            status_code=429,
            text="Too Many Requests",
            headers={"Retry-After": "0.01"},
            request=httpx.Request("GET", f"{GUESTY_BASE_URL}/test"),
        )

        mock_http_client.request = AsyncMock(return_value=rate_limit_resp)

        with pytest.raises(PMSRateLimitError, match="rate limit exceeded"):
            await guesty_provider._request("GET", "/test")

        assert mock_http_client.request.call_count == 4

    @pytest.mark.asyncio
    async def test_retry_after_header_parsed(self, guesty_provider, mock_http_client):
        """Test that Retry-After header is respected."""
        rate_limit_resp = httpx.Response(
            status_code=429,
            text="Too Many Requests",
            headers={"Retry-After": "0.01"},
            request=httpx.Request("GET", f"{GUESTY_BASE_URL}/test"),
        )
        success_resp = _make_response({"ok": True})

        mock_http_client.request = AsyncMock(
            side_effect=[rate_limit_resp, success_resp]
        )

        response = await guesty_provider._request("GET", "/test")
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_auth_error_not_retried(self, guesty_provider, mock_http_client):
        """Test that auth errors are not retried."""
        mock_http_client.request = AsyncMock(
            return_value=httpx.Response(
                status_code=401,
                text="Unauthorized",
                request=httpx.Request("GET", f"{GUESTY_BASE_URL}/test"),
            )
        )

        with pytest.raises(PMSAuthenticationError):
            await guesty_provider._request("GET", "/test")

        assert mock_http_client.request.call_count == 1

    @pytest.mark.asyncio
    async def test_network_error_raises_connection_error(
        self, guesty_provider, mock_http_client
    ):
        """Test that network errors raise PMSConnectionError."""
        mock_http_client.request = AsyncMock(side_effect=httpx.ConnectError("DNS fail"))

        with pytest.raises(PMSConnectionError, match="DNS fail"):
            await guesty_provider._request("GET", "/test")


# ---------------------------------------------------------------------------
# refresh_token
# ---------------------------------------------------------------------------


class TestRefreshToken:
    """Tests for GuestyProvider.refresh_token."""

    @pytest.mark.asyncio
    async def test_refresh_delegates_to_token_manager(
        self, guesty_provider, mock_token_manager
    ):
        """Test that refresh delegates to token manager."""
        mock_credential = MagicMock()
        result = await guesty_provider.refresh_token(mock_credential)

        mock_token_manager.invalidate_cache.assert_called_once()
        assert result.access_token == "test-token"
        assert result.refresh_token is None

    @pytest.mark.asyncio
    async def test_refresh_without_manager_raises(self, mock_http_client):
        """Test that refresh without manager raises error."""
        prov = GuestyProvider(http_client=mock_http_client)
        mock_credential = MagicMock()

        with pytest.raises(PMSAuthenticationError, match="token manager"):
            await prov.refresh_token(mock_credential)


# ---------------------------------------------------------------------------
# test_connection
# ---------------------------------------------------------------------------


class TestTestConnection:
    """Tests for GuestyProvider.test_connection."""

    @pytest.mark.asyncio
    async def test_returns_true_on_success(self, guesty_provider, mock_http_client):
        """Test that test_connection returns True on success."""
        mock_http_client.request = AsyncMock(
            return_value=_make_response(SAMPLE_LISTINGS_PAGE1)
        )

        assert await guesty_provider.test_connection() is True

    @pytest.mark.asyncio
    async def test_warns_on_zero_listings(
        self, guesty_provider, mock_http_client, caplog
    ):
        """Test that zero listings produces a warning."""
        mock_http_client.request = AsyncMock(
            return_value=_make_response({"results": []})
        )

        import logging

        with caplog.at_level(logging.WARNING):
            result = await guesty_provider.test_connection()

        assert result is True
        assert "zero listings" in caplog.text.lower()

    @pytest.mark.asyncio
    async def test_raises_on_failure(self, guesty_provider, mock_http_client):
        """Test that connection failures raise PMSConnectionError."""
        mock_http_client.request = AsyncMock(
            side_effect=httpx.ConnectError("unreachable")
        )

        with pytest.raises(PMSConnectionError):
            await guesty_provider.test_connection()


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------


class TestParseDate:
    """Tests for _parse_date helper."""

    def test_parses_iso_datetime(self):
        """Test parsing ISO datetime strings."""
        result = _parse_date("2026-03-01T14:00:00Z")
        assert result.year == 2026
        assert result.month == 3
        assert result.day == 1

    def test_parses_date_only(self):
        """Test parsing date-only strings."""
        result = _parse_date("2026-03-01")
        assert result == datetime(2026, 3, 1, tzinfo=UTC)

    def test_passes_through_datetime(self):
        """Test that datetime objects pass through."""
        dt = datetime(2026, 3, 1, tzinfo=UTC)
        assert _parse_date(dt) == dt

    def test_none_raises(self):
        """Test that None raises PMSProviderError."""
        with pytest.raises(PMSProviderError):
            _parse_date(None)

    def test_empty_raises(self):
        """Test that empty string raises PMSProviderError."""
        with pytest.raises(PMSProviderError):
            _parse_date("")


class TestParseRetryAfter:
    """Tests for _parse_retry_after helper."""

    def test_parses_numeric(self):
        """Test parsing numeric Retry-After header."""
        resp = httpx.Response(
            status_code=429,
            headers={"Retry-After": "30"},
            request=httpx.Request("GET", "https://example.com"),
        )
        assert _parse_retry_after(resp) == 30.0

    def test_returns_none_when_missing(self):
        """Test None when Retry-After header is absent."""
        resp = httpx.Response(
            status_code=429,
            request=httpx.Request("GET", "https://example.com"),
        )
        assert _parse_retry_after(resp) is None

    def test_returns_none_on_invalid(self):
        """Test None for non-numeric Retry-After."""
        resp = httpx.Response(
            status_code=429,
            headers={"Retry-After": "not-a-number"},
            request=httpx.Request("GET", "https://example.com"),
        )
        assert _parse_retry_after(resp) is None


class TestFormatAddress:
    """Tests for _format_address helper."""

    def test_full_address(self):
        """Test formatting a full address string."""
        assert _format_address({"full": "123 Main St"}) == "123 Main St"

    def test_partial_address(self):
        """Test formatting a partial address."""
        result = _format_address({"street": "123 Main", "city": "NYC"})
        assert result == "123 Main, NYC"

    def test_none_address(self):
        """Test that None address returns None."""
        assert _format_address(None) is None

    def test_empty_address(self):
        """Test that empty dict returns None."""
        assert _format_address({}) is None
