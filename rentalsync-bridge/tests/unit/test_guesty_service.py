# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for GuestyProvider."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

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
    DEFAULT_LOOKBACK_DAYS,
    GUESTY_BASE_URL,
    GuestyProvider,
    _extract_custom_data,
    _format_address,
    _parse_date,
    _parse_retry_after,
)

# ---------------------------------------------------------------------------
# Sample API responses
# ---------------------------------------------------------------------------


def _rel(days: int, suffix: str = "") -> str:
    """Build a reservation date string relative to now.

    Sample stays must stay inside the provider's default 365-day window,
    which is now applied client-side, so fixed calendar dates would rot.

    Args:
        days: Offset in days from now.
        suffix: Optional time suffix appended to the date.

    Returns:
        ISO date string, optionally with *suffix* appended.
    """
    return (datetime.now(UTC) + timedelta(days=days)).strftime("%Y-%m-%d") + suffix


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
            "checkIn": _rel(-10, "T14:00:00Z"),
            "checkOut": _rel(-6, "T11:00:00Z"),
            "listingId": "L1",
            "source": "airbnb",
            "confirmationCode": "ABC123",
            "nightsCount": 4,
            "guestsCount": 2,
            "money": {
                "totalPaid": 500,
                "balanceDue": 0,
                "currency": "USD",
            },
        },
        {
            "_id": "R2",
            "status": "checked_in",
            "guest": {"_id": "G2", "fullName": "Bob Jones"},
            "checkIn": _rel(-2),
            "checkOut": _rel(3),
            "listingId": "L1",
        },
        {
            "_id": "R3",
            "status": "inquiry",
            "guest": {"_id": "G3", "fullName": "Excluded"},
            "checkIn": _rel(20),
            "checkOut": _rel(24),
            "listingId": "L1",
        },
        {
            "_id": "R4",
            "status": "canceled",
            "guest": {"_id": "G4", "fullName": "Cancelled Guest"},
            "checkIn": _rel(40),
            "checkOut": _rel(44),
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
    mgr.invalidate_cache = AsyncMock()
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

    def test_has_separate_custom_fields(self, guesty_provider):
        """Test Guesty reports separate custom fields."""
        assert guesty_provider.has_separate_custom_fields is True


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
    async def test_custom_data_populated(self, guesty_provider, mock_http_client):
        """Test that custom_data is populated from reservation fields."""
        mock_http_client.request = AsyncMock(
            return_value=_make_response(SAMPLE_RESERVATIONS)
        )

        reservations = await guesty_provider.get_reservations("L1")
        r1 = next(r for r in reservations if r.pms_booking_id == "R1")

        assert r1.custom_data["source"] == "airbnb"
        assert r1.custom_data["confirmationCode"] == "ABC123"
        assert r1.custom_data["nightsCount"] == 4
        assert r1.custom_data["guestsCount"] == 2
        assert r1.custom_data["money_totalPaid"] == 500
        assert r1.custom_data["money_balanceDue"] == 0
        assert r1.custom_data["money_currency"] == "USD"

    @pytest.mark.asyncio
    async def test_empty_custom_data_when_no_extra_fields(
        self, guesty_provider, mock_http_client
    ):
        """Test custom_data is empty when reservation has no extra fields."""
        mock_http_client.request = AsyncMock(
            return_value=_make_response(SAMPLE_RESERVATIONS)
        )

        reservations = await guesty_provider.get_reservations("L1")
        r2 = next(r for r in reservations if r.pms_booking_id == "R2")

        assert r2.custom_data == {}

    @pytest.mark.asyncio
    async def test_status_mapping(self, guesty_provider, mock_http_client):
        """Test that Guesty statuses map to normalised statuses."""
        mock_http_client.request = AsyncMock(
            return_value=_make_response(SAMPLE_RESERVATIONS)
        )

        reservations = await guesty_provider.get_reservations("L1")
        statuses = {r.pms_booking_id: r.status for r in reservations}

        assert statuses["R1"] == "confirmed"
        assert statuses["R2"] == "checked_in"
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
    async def test_no_undocumented_date_filters_sent(
        self, guesty_provider, mock_http_client
    ):
        """Test that undocumented stay-date query params are not sent.

        Guesty's v1 ``/reservations`` OpenAPI definition exposes only a
        generic ``filters`` array -- there are no ``checkIn``/``checkOut``
        or ``startDate``/``endDate`` query parameters -- and whether the API
        rejects or silently ignores unknown parameters is undocumented.
        """
        mock_http_client.request = AsyncMock(
            return_value=_make_response({"results": [], "count": 0})
        )

        start = datetime(2026, 1, 1, tzinfo=UTC)
        end = datetime(2026, 12, 31, tzinfo=UTC)
        await guesty_provider.get_reservations("L1", start_date=start, end_date=end)

        call_kwargs = mock_http_client.request.call_args
        params = call_kwargs.kwargs.get("params") or call_kwargs[1].get("params", {})
        assert params["listingId"] == "L1"
        for unsupported in ("checkIn", "checkOut", "startDate", "endDate"):
            assert unsupported not in params

    @pytest.mark.asyncio
    async def test_window_filter_applied_client_side(
        self, guesty_provider, mock_http_client
    ):
        """Test stays are filtered to the requested window after fetching."""
        payload = {
            "count": 3,
            "results": [
                {
                    "_id": "BEFORE",
                    "status": "confirmed",
                    "checkIn": _rel(-40),
                    "checkOut": _rel(-35),
                    "listingId": "L1",
                },
                {
                    "_id": "IN-PROGRESS",
                    "status": "confirmed",
                    "checkIn": _rel(-2),
                    "checkOut": _rel(3),
                    "listingId": "L1",
                },
                {
                    "_id": "AFTER",
                    "status": "confirmed",
                    "checkIn": _rel(40),
                    "checkOut": _rel(45),
                    "listingId": "L1",
                },
            ],
        }
        mock_http_client.request = AsyncMock(return_value=_make_response(payload))

        now = datetime.now(UTC)
        reservations = await guesty_provider.get_reservations(
            "L1",
            start_date=now - timedelta(days=5),
            end_date=now + timedelta(days=5),
        )

        # A stay straddling "now" must survive: that is the issue #99 defect.
        assert {r.pms_booking_id for r in reservations} == {"IN-PROGRESS"}

    @pytest.mark.asyncio
    async def test_default_window_includes_in_progress_stays(
        self, guesty_provider, mock_http_client
    ):
        """Test the default window reaches a year into the past."""
        payload = {
            "count": 2,
            "results": [
                {
                    "_id": "OLD-BUT-RUNNING",
                    "status": "confirmed",
                    "checkIn": _rel(-(DEFAULT_LOOKBACK_DAYS - 2)),
                    "checkOut": _rel(2),
                    "listingId": "L1",
                },
                {
                    "_id": "ANCIENT",
                    "status": "confirmed",
                    "checkIn": _rel(-(DEFAULT_LOOKBACK_DAYS + 20)),
                    "checkOut": _rel(-(DEFAULT_LOOKBACK_DAYS + 10)),
                    "listingId": "L1",
                },
            ],
        }
        mock_http_client.request = AsyncMock(return_value=_make_response(payload))

        reservations = await guesty_provider.get_reservations("L1")

        ids = {r.pms_booking_id for r in reservations}
        assert DEFAULT_LOOKBACK_DAYS == 365
        assert "OLD-BUT-RUNNING" in ids
        assert "ANCIENT" not in ids


# ---------------------------------------------------------------------------
# get_reservations pagination
# ---------------------------------------------------------------------------


def _reservation(booking_id):
    """Build a raw in-window Guesty reservation dict.

    Args:
        booking_id: Reservation ``_id``.

    Returns:
        Raw reservation dict.
    """
    return {
        "_id": booking_id,
        "status": "confirmed",
        "guest": {"_id": f"g-{booking_id}", "fullName": f"Guest {booking_id}"},
        "checkIn": _rel(-1),
        "checkOut": _rel(2),
        "listingId": "L1",
    }


def _mock_provider(handler):
    """Build a GuestyProvider wired to a MockTransport handler.

    Args:
        handler: Callable taking an httpx.Request and returning a Response.

    Returns:
        Configured GuestyProvider.
    """
    token_manager = AsyncMock()
    token_manager.get_token = AsyncMock(return_value="tok")
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url=GUESTY_BASE_URL,
    )
    return GuestyProvider(token_manager=token_manager, http_client=client)


class TestReservationPagination:
    """Pagination tests for GuestyProvider.get_reservations."""

    @pytest.mark.asyncio
    async def test_accumulates_pages_driven_by_count(self):
        """Test every record is fetched when ``count`` exceeds one page."""
        total = 250
        requested_skips = []

        def handler(request):
            """Serve full pages up to ``total`` records.

            Args:
                request: Outgoing request.

            Returns:
                Mocked JSON response.
            """
            skip = int(request.url.params["skip"])
            requested_skips.append(skip)
            page = [_reservation(f"R{i}") for i in range(skip, min(skip + 100, total))]
            return httpx.Response(200, json={"count": total, "results": page})

        provider = _mock_provider(handler)
        reservations = await provider.get_reservations("L1")
        await provider.aclose()

        assert len(reservations) == total
        assert requested_skips == [0, 100, 200]

    @pytest.mark.asyncio
    async def test_short_page_is_not_treated_as_end(self):
        """Test a short non-final page does not truncate the snapshot.

        A server-side page cap smaller than the requested limit must not be
        mistaken for end-of-results: the sync prune logic treats missing
        bookings as cancelled, so truncation silently retires live stays.
        """
        total = 120
        page_size = 40

        def handler(request):
            """Serve 40-record pages even though 100 are requested.

            Args:
                request: Outgoing request.

            Returns:
                Mocked JSON response.
            """
            skip = int(request.url.params["skip"])
            page = [
                _reservation(f"R{i}") for i in range(skip, min(skip + page_size, total))
            ]
            return httpx.Response(200, json={"count": total, "results": page})

        provider = _mock_provider(handler)
        reservations = await provider.get_reservations("L1")
        await provider.aclose()

        assert len(reservations) == total

    @pytest.mark.asyncio
    async def test_later_page_failure_returns_nothing(self):
        """Test a failure on a later page raises instead of truncating."""

        def handler(request):
            """Fail the second page.

            Args:
                request: Outgoing request.

            Returns:
                Mocked JSON response.
            """
            skip = int(request.url.params["skip"])
            if skip == 0:
                page = [_reservation(f"R{i}") for i in range(100)]
                return httpx.Response(200, json={"count": 200, "results": page})
            return httpx.Response(500, json={"error": "boom"})

        provider = _mock_provider(handler)
        with pytest.raises(PMSProviderError):
            await provider.get_reservations("L1")
        await provider.aclose()

    @pytest.mark.asyncio
    async def test_missing_count_falls_back_to_short_page(self):
        """Test the short-page heuristic is used when ``count`` is absent."""
        pages = [
            {"results": [_reservation(f"R{i}") for i in range(100)]},
            {"results": [_reservation("R100")]},
        ]
        calls = []

        def handler(request):
            """Serve the prepared pages without ``count`` metadata.

            Args:
                request: Outgoing request.

            Returns:
                Mocked JSON response.
            """
            index = len(calls)
            calls.append(request)
            return httpx.Response(200, json=pages[index])

        provider = _mock_provider(handler)
        reservations = await provider.get_reservations("L1")
        await provider.aclose()

        assert len(calls) == 2
        assert len(reservations) == 101

    @pytest.mark.asyncio
    async def test_empty_page_raises_instead_of_partial(self):
        """Test an inconsistent ``count`` raises rather than truncating.

        An empty page while records remain outstanding means the API will not
        serve the rest of the snapshot.  Returning the records gathered so far
        would let the sync prune logic cancel every unseen future booking, so
        the run must fail and preserve the existing snapshot instead.
        """
        calls = []

        def handler(request):
            """Claim more records than are ever served.

            Args:
                request: Outgoing request.

            Returns:
                Mocked JSON response.
            """
            calls.append(request)
            if request.url.params["skip"] != "0":
                return httpx.Response(200, json={"count": 9999, "results": []})
            page = [_reservation(f"R{i}") for i in range(100)]
            return httpx.Response(200, json={"count": 9999, "results": page})

        provider = _mock_provider(handler)
        with pytest.raises(PMSProviderError, match="partial snapshot"):
            await provider.get_reservations("L1")
        await provider.aclose()

        assert len(calls) == 2

    @pytest.mark.asyncio
    async def test_exact_count_completes_without_empty_page(self):
        """Test a run ending exactly on ``count`` does not raise.

        The empty-page guard must not turn a legitimate completion into a
        spurious error when the final page lands exactly on ``count``.
        """
        total = 200
        calls = []

        def handler(request):
            """Serve exactly ``total`` records in full pages.

            Args:
                request: Outgoing request.

            Returns:
                Mocked JSON response.
            """
            calls.append(request)
            skip = int(request.url.params["skip"])
            page = [_reservation(f"R{i}") for i in range(skip, min(skip + 100, total))]
            return httpx.Response(200, json={"count": total, "results": page})

        provider = _mock_provider(handler)
        reservations = await provider.get_reservations("L1")
        await provider.aclose()

        assert len(calls) == 2
        assert len(reservations) == total

    @pytest.mark.asyncio
    async def test_missing_count_empty_first_page_returns_empty(self):
        """Test the ``count``-less fallback still ends on an empty page."""
        calls = []

        def handler(request):
            """Serve a single empty page without ``count`` metadata.

            Args:
                request: Outgoing request.

            Returns:
                Mocked JSON response.
            """
            calls.append(request)
            return httpx.Response(200, json={"results": []})

        provider = _mock_provider(handler)
        reservations = await provider.get_reservations("L1")
        await provider.aclose()

        assert len(calls) == 1
        assert reservations == []

    @pytest.mark.asyncio
    async def test_zero_count_returns_empty(self):
        """Test a listing with no reservations completes cleanly."""

        def handler(request):
            """Report zero records.

            Args:
                request: Outgoing request.

            Returns:
                Mocked JSON response.
            """
            return httpx.Response(200, json={"count": 0, "results": []})

        provider = _mock_provider(handler)
        reservations = await provider.get_reservations("L1")
        await provider.aclose()

        assert reservations == []

    @pytest.mark.asyncio
    async def test_page_cap_raises(self, monkeypatch):
        """Test exceeding the page cap raises instead of truncating."""
        import src.providers.guesty.service as guesty_service

        monkeypatch.setattr(guesty_service, "MAX_RESERVATION_PAGES", 3)

        def handler(request):
            """Always claim there is more to fetch.

            Args:
                request: Outgoing request.

            Returns:
                Mocked JSON response.
            """
            skip = int(request.url.params["skip"])
            page = [_reservation(f"R{skip + i}") for i in range(100)]
            return httpx.Response(200, json={"count": 100_000, "results": page})

        provider = _mock_provider(handler)
        with pytest.raises(PMSProviderError, match="exceeded 3 pages"):
            await provider.get_reservations("L1")
        await provider.aclose()


class TestReservationWindowBounds:
    """Window-bound handling for GuestyProvider.get_reservations.

    The provider interface does not require timezone-aware bounds, and the
    window filter is now applied client-side against UTC-aware parsed dates,
    so naive bounds must be coerced rather than raising ``TypeError``.
    """

    @staticmethod
    def _single_page_provider():
        """Build a provider serving one in-window reservation.

        Returns:
            Configured GuestyProvider.
        """

        def handler(request):
            """Serve a single reservation page.

            Args:
                request: Outgoing request.

            Returns:
                Mocked JSON response.
            """
            return httpx.Response(
                200, json={"count": 1, "results": [_reservation("R1")]}
            )

        return _mock_provider(handler)

    @pytest.mark.asyncio
    async def test_aware_bounds(self):
        """Test timezone-aware bounds select the overlapping stay."""
        now = datetime.now(UTC)
        provider = self._single_page_provider()
        reservations = await provider.get_reservations(
            "L1",
            start_date=now - timedelta(days=10),
            end_date=now + timedelta(days=10),
        )
        await provider.aclose()

        assert len(reservations) == 1

    @pytest.mark.asyncio
    async def test_naive_bounds(self):
        """Test naive bounds are treated as UTC instead of raising."""
        now = datetime.now(UTC).replace(tzinfo=None)
        provider = self._single_page_provider()
        reservations = await provider.get_reservations(
            "L1",
            start_date=now - timedelta(days=10),
            end_date=now + timedelta(days=10),
        )
        await provider.aclose()

        assert len(reservations) == 1

    @pytest.mark.asyncio
    async def test_mixed_naive_and_aware_bounds(self):
        """Test a naive start combined with an aware end is accepted."""
        now = datetime.now(UTC)
        provider = self._single_page_provider()
        reservations = await provider.get_reservations(
            "L1",
            start_date=(now - timedelta(days=10)).replace(tzinfo=None),
            end_date=now + timedelta(days=10),
        )
        await provider.aclose()

        assert len(reservations) == 1

    @pytest.mark.asyncio
    async def test_naive_bounds_still_filter(self):
        """Test naive bounds outside the stay still exclude it."""
        now = datetime.now(UTC).replace(tzinfo=None)
        provider = self._single_page_provider()
        reservations = await provider.get_reservations(
            "L1",
            start_date=now - timedelta(days=60),
            end_date=now - timedelta(days=30),
        )
        await provider.aclose()

        assert reservations == []


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
    @patch("asyncio.sleep", new_callable=AsyncMock)
    async def test_retries_on_429(self, _mock_sleep, guesty_provider, mock_http_client):
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
    @patch("asyncio.sleep", new_callable=AsyncMock)
    async def test_raises_after_max_retries(
        self, _mock_sleep, guesty_provider, mock_http_client
    ):
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
    @patch("asyncio.sleep", new_callable=AsyncMock)
    async def test_retry_after_header_parsed(
        self, _mock_sleep, guesty_provider, mock_http_client
    ):
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

        mock_token_manager.invalidate_cache.assert_awaited_once()
        assert result.access_token == "test-token"
        assert result.refresh_token is None

    @pytest.mark.asyncio
    async def test_refresh_without_manager_raises(self, mock_http_client):
        """Test that refresh without manager and missing creds raises."""
        prov = GuestyProvider(http_client=mock_http_client)
        mock_credential = MagicMock()
        mock_credential.client_id = None
        mock_credential.client_secret = None

        with pytest.raises(PMSAuthenticationError, match="client_id"):
            await prov.refresh_token(mock_credential)

    @pytest.mark.asyncio
    async def test_refresh_direct_http_success(self, mock_http_client):
        """Test direct HTTP refresh returns TokenResult on 200."""
        prov = GuestyProvider(http_client=mock_http_client)
        mock_credential = MagicMock()
        mock_credential.client_id = "cid"
        mock_credential.client_secret = "csecret"

        resp = httpx.Response(
            status_code=200,
            json={"access_token": "new-tok", "expires_in": 3600},
            request=httpx.Request("POST", "https://example.com"),
        )
        with patch("httpx.AsyncClient") as mock_cls:
            ctx = AsyncMock()
            ctx.post = AsyncMock(return_value=resp)
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=ctx)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await prov.refresh_token(mock_credential)

        assert result.access_token == "new-tok"
        assert result.refresh_token is None
        assert result.expires_at is not None

    @pytest.mark.asyncio
    async def test_refresh_direct_http_non_200(self, mock_http_client):
        """Test direct HTTP refresh raises on non-200 response."""
        prov = GuestyProvider(http_client=mock_http_client)
        mock_credential = MagicMock()
        mock_credential.client_id = "cid"
        mock_credential.client_secret = "csecret"

        resp = httpx.Response(
            status_code=500,
            text="Internal Server Error",
            request=httpx.Request("POST", "https://example.com"),
        )
        with patch("httpx.AsyncClient") as mock_cls:
            ctx = AsyncMock()
            ctx.post = AsyncMock(return_value=resp)
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=ctx)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            with pytest.raises(PMSAuthenticationError, match="failed"):
                await prov.refresh_token(mock_credential)

    @pytest.mark.asyncio
    async def test_refresh_direct_http_missing_access_token(self, mock_http_client):
        """Test direct HTTP refresh raises when access_token missing."""
        prov = GuestyProvider(http_client=mock_http_client)
        mock_credential = MagicMock()
        mock_credential.client_id = "cid"
        mock_credential.client_secret = "csecret"

        resp = httpx.Response(
            status_code=200,
            json={"expires_in": 3600},
            request=httpx.Request("POST", "https://example.com"),
        )
        with patch("httpx.AsyncClient") as mock_cls:
            ctx = AsyncMock()
            ctx.post = AsyncMock(return_value=resp)
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=ctx)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            with pytest.raises(PMSAuthenticationError, match="missing access_token"):
                await prov.refresh_token(mock_credential)

    @pytest.mark.asyncio
    async def test_refresh_direct_http_network_error(self, mock_http_client):
        """Test direct HTTP refresh raises on network error."""
        prov = GuestyProvider(http_client=mock_http_client)
        mock_credential = MagicMock()
        mock_credential.client_id = "cid"
        mock_credential.client_secret = "csecret"

        with patch("httpx.AsyncClient") as mock_cls:
            ctx = AsyncMock()
            ctx.post = AsyncMock(side_effect=httpx.ConnectError("connection refused"))
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=ctx)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            with pytest.raises(PMSConnectionError, match="Failed to connect"):
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


class TestExtractCustomData:
    """Tests for _extract_custom_data helper."""

    def test_top_level_keys(self):
        """Test extraction of top-level reservation fields."""
        raw = {
            "source": "booking.com",
            "confirmationCode": "XYZ",
            "nightsCount": 3,
            "guestsCount": 1,
        }
        result = _extract_custom_data(raw)
        assert result == {
            "source": "booking.com",
            "confirmationCode": "XYZ",
            "nightsCount": 3,
            "guestsCount": 1,
        }

    def test_money_fields(self):
        """Test extraction of nested money fields."""
        raw = {
            "money": {
                "totalPaid": 250.50,
                "balanceDue": 0,
                "currency": "EUR",
            },
        }
        result = _extract_custom_data(raw)
        assert result["money_totalPaid"] == 250.50
        assert result["money_balanceDue"] == 0
        assert result["money_currency"] == "EUR"

    def test_notes_from_dict_objects(self):
        """Test notes extraction from dict note objects."""
        raw = {
            "notes": [
                {"note": "Early check-in"},
                {"note": "Allergic to cats"},
            ],
        }
        result = _extract_custom_data(raw)
        assert result["notes"] == "Early check-in; Allergic to cats"

    def test_notes_from_strings(self):
        """Test notes extraction from string entries."""
        raw = {"notes": ["First note", "Second note"]}
        result = _extract_custom_data(raw)
        assert result["notes"] == "First note; Second note"

    def test_empty_notes_not_included(self):
        """Test that empty notes list produces no key."""
        assert "notes" not in _extract_custom_data({"notes": []})
        assert "notes" not in _extract_custom_data({"notes": [{"note": ""}]})

    def test_none_values_skipped(self):
        """Test that None field values are not included."""
        raw = {"source": None, "confirmationCode": "OK"}
        result = _extract_custom_data(raw)
        assert "source" not in result
        assert result["confirmationCode"] == "OK"

    def test_empty_raw_returns_empty(self):
        """Test that empty raw dict returns empty custom_data."""
        assert _extract_custom_data({}) == {}

    def test_non_dict_money_ignored(self):
        """Test that non-dict money value is ignored."""
        assert _extract_custom_data({"money": "invalid"}) == {}
