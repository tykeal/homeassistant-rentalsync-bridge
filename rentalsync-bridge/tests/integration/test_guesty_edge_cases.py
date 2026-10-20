# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Edge case integration tests for Guesty PMS provider.

Covers: guest 404 fallback, listing with no rooms, token limit
exhaustion and deferral, paginated multi-page results, and cancelled
reservation handling.
"""

import logging
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import select
from src.models.booking import Booking
from src.providers.base import (
    PMSReservation,
    TokenRateLimitError,
)
from src.providers.guesty.auth import GuestyTokenManager
from src.providers.guesty.service import GuestyProvider
from src.services.calendar_service import CalendarCache
from src.services.sync_service import SyncService, SyncServiceError

from tests.conftest import make_listing, make_room


class TestGuest404Fallback:
    """When get_guest returns None, booking uses fallback name."""

    async def test_guest_not_found_uses_booking_id(
        self, async_session, async_session_factory
    ):
        """Test guest 404 falls back to booking ID as event title."""
        listing = make_listing(
            pms_id="edge_prop_1",
            name="Edge Property",
            slug="edge-prop-1",
        )
        async_session.add(listing)
        await async_session.commit()
        await async_session.refresh(listing)

        now = datetime.now(UTC)
        mock_provider = AsyncMock()
        mock_provider.provider_type = "guesty"
        mock_provider.has_separate_custom_fields = True
        mock_provider.get_reservations = AsyncMock(
            return_value=[
                PMSReservation(
                    pms_booking_id="EDGE_BK001",
                    listing_pms_id="edge_prop_1",
                    guest_name=None,
                    guest_id="missing_guest_id",
                    check_in=now + timedelta(days=5),
                    check_out=now + timedelta(days=8),
                    status="confirmed",
                    room_ids=(),
                    custom_data={},
                ),
            ]
        )
        # get_guest returns None (404 scenario)
        mock_provider.get_guest = AsyncMock(return_value=None)
        mock_provider.get_custom_fields = AsyncMock(return_value={})

        sync = SyncService(
            async_session,
            calendar_cache=CalendarCache(ttl_seconds=0),
            session_factory=async_session_factory,
        )
        counts = await sync.sync_listing(listing, mock_provider)
        assert counts["inserted"] == 1

        result = await async_session.execute(
            select(Booking).where(Booking.listing_id == listing.id)
        )
        booking = result.scalars().first()
        # guest_name stays None when lookup fails
        assert booking is not None
        assert booking.guest_name is None
        # event_title falls back to booking ID
        assert booking.event_title == "EDGE_BK001"


class TestListingWithNoRooms:
    """Listing with no rooms uses implicit room handling."""

    async def test_reservation_without_room_ids(
        self, async_session, async_session_factory
    ):
        """Test reservation with no room IDs stores NULL room."""
        listing = make_listing(
            pms_id="no_rooms_prop",
            name="No Rooms Property",
            slug="no-rooms-prop",
        )
        async_session.add(listing)
        await async_session.commit()
        await async_session.refresh(listing)

        now = datetime.now(UTC)
        mock_provider = AsyncMock()
        mock_provider.provider_type = "guesty"
        mock_provider.has_separate_custom_fields = True
        mock_provider.get_reservations = AsyncMock(
            return_value=[
                PMSReservation(
                    pms_booking_id="NO_ROOM_BK",
                    listing_pms_id="no_rooms_prop",
                    guest_name="Roomless Guest",
                    guest_id=None,
                    check_in=now + timedelta(days=3),
                    check_out=now + timedelta(days=6),
                    status="confirmed",
                    room_ids=(),
                    custom_data={},
                ),
            ]
        )
        mock_provider.get_guest = AsyncMock(return_value=None)
        mock_provider.get_custom_fields = AsyncMock(return_value={})

        sync = SyncService(
            async_session,
            calendar_cache=CalendarCache(ttl_seconds=0),
            session_factory=async_session_factory,
        )
        counts = await sync.sync_listing(listing, mock_provider)
        assert counts["inserted"] == 1

        result = await async_session.execute(
            select(Booking).where(Booking.listing_id == listing.id)
        )
        booking = result.scalars().first()
        assert booking is not None
        assert booking.room_id is None
        assert booking.pms_booking_id == "NO_ROOM_BK"


class TestTokenLimitExhaustion:
    """Token rate limit (5/day) is enforced."""

    async def test_token_limit_raises_error(self):
        """Test exceeding token limit raises TokenRateLimitError."""
        mock_repo = AsyncMock()
        mock_repo.get_token_request_count = AsyncMock(return_value=5)
        cred = AsyncMock()
        cred.token_request_window_start = datetime.now(UTC) - timedelta(hours=1)
        # Ensure DB-reload path doesn't shortcut with a cached token
        cred.access_token = None
        cred.token_expires_at = None
        mock_repo.get_credential = AsyncMock(return_value=cred)

        tm = GuestyTokenManager(
            client_id="cid",
            client_secret="csec",
            credential_repo=mock_repo,
            credential_id=1,
        )
        # Clear cache to force rate check
        await tm.invalidate_cache()

        with pytest.raises(TokenRateLimitError):
            await tm.get_token()


class TestPaginatedResults:
    """Multi-page paginated results from Guesty API."""

    async def test_paginated_listings(self):
        """Test multi-page listing pagination returns all results."""
        mock_tm = AsyncMock()
        mock_tm.get_token = AsyncMock(return_value="test_token")
        mock_http = AsyncMock(spec=httpx.AsyncClient)

        # Page 1: 100 results, page 2: 50 results
        page1_results = [
            {"_id": f"L{i:03d}", "title": f"Listing {i}", "timezone": "UTC"}
            for i in range(100)
        ]
        page2_results = [
            {"_id": f"L{i:03d}", "title": f"Listing {i}", "timezone": "UTC"}
            for i in range(100, 150)
        ]

        responses = [
            httpx.Response(200, json={"results": page1_results}),
            httpx.Response(200, json={"results": page2_results}),
        ]
        mock_http.request = AsyncMock(side_effect=responses)

        provider = GuestyProvider(token_manager=mock_tm, http_client=mock_http)
        listings = await provider.get_listings()
        assert len(listings) == 150
        assert mock_http.request.call_count == 2

    async def test_paginated_reservations(self):
        """Test multi-page reservation pagination returns all results."""
        mock_tm = AsyncMock()
        mock_tm.get_token = AsyncMock(return_value="test_token")
        mock_http = AsyncMock(spec=httpx.AsyncClient)

        now = datetime.now(UTC)
        page1 = [
            {
                "_id": f"R{i:03d}",
                "status": "confirmed",
                "guest": {"fullName": f"Guest {i}", "_id": f"g{i}"},
                "checkIn": (now + timedelta(days=i)).isoformat(),
                "checkOut": (now + timedelta(days=i + 3)).isoformat(),
                "listingId": "PROP001",
            }
            for i in range(100)
        ]
        page2 = [
            {
                "_id": f"R{i:03d}",
                "status": "confirmed",
                "guest": {"fullName": f"Guest {i}", "_id": f"g{i}"},
                "checkIn": (now + timedelta(days=i)).isoformat(),
                "checkOut": (now + timedelta(days=i + 3)).isoformat(),
                "listingId": "PROP001",
            }
            for i in range(100, 130)
        ]

        responses = [
            httpx.Response(200, json={"_id": "PROP001", "title": "Prop"}),
            httpx.Response(200, json={"results": page1}),
            httpx.Response(200, json={"results": page2}),
        ]
        mock_http.request = AsyncMock(side_effect=responses)

        provider = GuestyProvider(token_manager=mock_tm, http_client=mock_http)
        reservations = await provider.get_reservations("PROP001")
        assert len(reservations) == 130


class TestCancelledReservation:
    """Cancelled reservations are handled correctly."""

    async def test_disappearing_reservation_marked_cancelled(
        self, async_session, async_session_factory
    ):
        """Test missing reservation on re-sync is marked cancelled."""
        listing = make_listing(
            pms_id="cancel_prop",
            name="Cancel Property",
            slug="cancel-prop",
        )
        async_session.add(listing)
        await async_session.commit()
        await async_session.refresh(listing)

        now = datetime.now(UTC)

        # First sync: two bookings
        mock_provider = AsyncMock()
        mock_provider.provider_type = "guesty"
        mock_provider.has_separate_custom_fields = True
        mock_provider.get_reservations = AsyncMock(
            return_value=[
                PMSReservation(
                    pms_booking_id=f"CAN_BK{i:03d}",
                    listing_pms_id="cancel_prop",
                    guest_name=f"Cancel Guest {i}",
                    guest_id=None,
                    check_in=now + timedelta(days=i * 5),
                    check_out=now + timedelta(days=i * 5 + 3),
                    status="confirmed",
                    room_ids=(),
                    custom_data={},
                )
                for i in range(1, 3)
            ]
        )
        mock_provider.get_guest = AsyncMock(return_value=None)
        mock_provider.get_custom_fields = AsyncMock(return_value={})

        sync = SyncService(
            async_session,
            calendar_cache=CalendarCache(ttl_seconds=0),
            session_factory=async_session_factory,
        )
        counts1 = await sync.sync_listing(listing, mock_provider)
        assert counts1["inserted"] == 2

        # Second sync: only one booking remains
        mock_provider.get_reservations = AsyncMock(
            return_value=[
                PMSReservation(
                    pms_booking_id="CAN_BK001",
                    listing_pms_id="cancel_prop",
                    guest_name="Cancel Guest 1",
                    guest_id=None,
                    check_in=now + timedelta(days=5),
                    check_out=now + timedelta(days=8),
                    status="confirmed",
                    room_ids=(),
                    custom_data={},
                ),
            ]
        )

        counts2 = await sync.sync_listing(listing, mock_provider)
        assert counts2["cancelled"] == 1

        result = await async_session.execute(
            select(Booking)
            .where(Booking.listing_id == listing.id)
            .where(Booking.status == "cancelled")
        )
        cancelled = list(result.scalars().all())
        assert len(cancelled) == 1
        assert cancelled[0].pms_booking_id == "CAN_BK002"

    async def test_explicitly_cancelled_status_persisted(
        self, async_session, async_session_factory
    ):
        """Test explicit cancellation retires an existing booking.

        A cancelled reservation with no stored booking is never inserted,
        otherwise purged cancellations would be resurrected on every poll.
        """
        listing = make_listing(
            pms_id="explicit_cancel",
            name="Explicit Cancel Property",
            slug="explicit-cancel",
        )
        async_session.add(listing)
        await async_session.commit()
        await async_session.refresh(listing)

        now = datetime.now(UTC)

        def _reservation(status: str) -> PMSReservation:
            """Build the reservation DTO with the requested status."""
            return PMSReservation(
                pms_booking_id="EX_CANCEL",
                listing_pms_id="explicit_cancel",
                guest_name="Cancelled Guest",
                guest_id=None,
                check_in=now + timedelta(days=10),
                check_out=now + timedelta(days=13),
                status=status,
                room_ids=(),
                custom_data={},
            )

        mock_provider = AsyncMock()
        mock_provider.provider_type = "guesty"
        mock_provider.has_separate_custom_fields = True
        mock_provider.get_reservations = AsyncMock(
            return_value=[_reservation("cancelled")]
        )
        mock_provider.get_guest = AsyncMock(return_value=None)
        mock_provider.get_custom_fields = AsyncMock(return_value={})

        sync = SyncService(
            async_session,
            calendar_cache=CalendarCache(ttl_seconds=0),
            session_factory=async_session_factory,
        )
        counts = await sync.sync_listing(listing, mock_provider)
        assert counts["inserted"] == 0

        result = await async_session.execute(
            select(Booking).where(Booking.listing_id == listing.id)
        )
        assert result.scalars().first() is None

        mock_provider.get_reservations = AsyncMock(
            return_value=[_reservation("confirmed")]
        )
        counts = await sync.sync_listing(listing, mock_provider)
        assert counts["inserted"] == 1

        mock_provider.get_reservations = AsyncMock(
            return_value=[_reservation("cancelled")]
        )
        counts = await sync.sync_listing(listing, mock_provider)
        assert counts["cancelled"] == 1

        result = await async_session.execute(
            select(Booking).where(Booking.listing_id == listing.id)
        )
        booking = result.scalars().first()
        assert booking is not None
        assert booking.status == "cancelled"


class TestCrossListingContamination:
    """Reservations must not leak between listings (issue #111).

    Guesty's ``/v1/reservations`` endpoint silently ignores query parameters
    it does not recognise, so every listing used to receive -- and store --
    the whole account's reservation set, producing N duplicate rows per real
    booking and a spurious "Room X not found" warning for every foreign row.
    """

    async def test_each_listing_stores_only_its_own_bookings(
        self, async_session, async_session_factory, caplog
    ):
        """Test syncing two listings from one account keeps them separate."""
        now = datetime.now(UTC)
        account = {
            "count": 4,
            "results": [
                {
                    "_id": f"RES-{listing_id}-{n}",
                    "status": "confirmed",
                    "guest": {"_id": f"g-{listing_id}-{n}", "fullName": "Guest"},
                    "checkIn": (now + timedelta(days=n)).isoformat(),
                    "checkOut": (now + timedelta(days=n + 2)).isoformat(),
                    "listingId": listing_id,
                }
                for listing_id in ("cross_a", "cross_b")
                for n in (1, 5)
            ],
        }

        def handler(request: httpx.Request) -> httpx.Response:
            """Serve single-unit listing documents and the whole account.

            Args:
                request: Outgoing request.

            Returns:
                Mocked JSON response.
            """
            if request.url.path.startswith("/v1/listings/"):
                listing_id = request.url.path.rsplit("/", 1)[-1]
                return httpx.Response(200, json={"_id": listing_id, "title": "X"})
            return httpx.Response(200, json=account)

        token_manager = AsyncMock()
        token_manager.get_token = AsyncMock(return_value="tok")
        provider = GuestyProvider(
            token_manager=token_manager,
            http_client=httpx.AsyncClient(
                transport=httpx.MockTransport(handler),
                base_url="https://open-api.guesty.com",
            ),
        )
        provider.get_custom_fields = AsyncMock(return_value={})

        listings = []
        for pms_id in ("cross_a", "cross_b"):
            listing = make_listing(pms_id=pms_id, name=pms_id, slug=pms_id)
            async_session.add(listing)
            await async_session.commit()
            await async_session.refresh(listing)
            async_session.add(
                make_room(listing_id=listing.id, pms_room_id=pms_id, slug=f"{pms_id}-r")
            )
            await async_session.commit()
            listings.append(listing)

        sync = SyncService(
            async_session,
            calendar_cache=CalendarCache(ttl_seconds=0),
            session_factory=async_session_factory,
        )
        with caplog.at_level(logging.INFO, logger="src.services.sync_service"):
            for listing in listings:
                await sync.sync_listing(listing, provider)
        await provider.aclose()

        for listing in listings:
            result = await async_session.execute(
                select(Booking).where(Booking.listing_id == listing.id)
            )
            bookings = result.scalars().all()
            assert len(bookings) == 2
            assert all(listing.pms_id in b.pms_booking_id for b in bookings)
            assert all(b.room_id is not None for b in bookings)

        assert "not found for booking" not in caplog.text


class TestDefaultFieldProjection:
    """Guesty's default field projection omits ``status`` (issue #121).

    ``GET /v1/reservations`` returns a fixed default projection when the
    ``fields`` query parameter is omitted, and ``status`` is not part of it.
    That is what shipped cancelled stays to door locks, and then -- once the
    "assume confirmed" fallback was removed in v0.5.2 -- emptied calendars.
    https://open-api-docs.guesty.com/docs/how-to-search-for-reservations
    """

    @staticmethod
    def _provider(handler):
        """Build a Guesty provider wired to *handler*.

        Args:
            handler: Callable taking an httpx.Request and returning a
                Response.

        Returns:
            Configured GuestyProvider with custom fields stubbed out.
        """
        token_manager = AsyncMock()
        token_manager.get_token = AsyncMock(return_value="tok")
        provider = GuestyProvider(
            token_manager=token_manager,
            http_client=httpx.AsyncClient(
                transport=httpx.MockTransport(handler),
                base_url="https://open-api.guesty.com",
            ),
        )
        provider.get_custom_fields = AsyncMock(return_value={})
        provider.get_guest = AsyncMock(return_value=None)
        return provider

    async def test_default_projection_keeps_last_good_bookings(
        self, async_session, async_session_factory
    ):
        """Test a status-less response leaves stored bookings intact.

        The fetch must abort before SyncService receives a snapshot, so the
        prune loop never runs and the previously-synced calendar keeps
        serving.  An empty calendar would stop working door codes.
        """
        now = datetime.now(UTC)
        listing = make_listing(
            pms_id="proj_prop",
            name="Projection Property",
            slug="proj-prop",
        )
        async_session.add(listing)
        await async_session.commit()
        await async_session.refresh(listing)
        async_session.add(
            make_room(
                listing_id=listing.id,
                pms_room_id="proj_prop",
                slug="proj-prop-r",
            )
        )
        await async_session.commit()

        healthy = {
            "count": 1,
            "results": [
                {
                    "_id": "PROJ-1",
                    "status": "confirmed",
                    "guest": {"_id": "g1", "fullName": "Guest"},
                    "checkIn": (now + timedelta(days=1)).isoformat(),
                    "checkOut": (now + timedelta(days=4)).isoformat(),
                    "listingId": "proj_prop",
                }
            ],
        }
        # Guesty's documented DEFAULT projection: same booking, no status.
        default_projection = {
            "count": 1,
            "results": [
                {
                    "_id": "PROJ-1",
                    "accountId": "acct-1",
                    "guestId": "g1",
                    "listingId": "proj_prop",
                    "listing": {"_id": "proj_prop", "title": "Projection Property"},
                    "checkIn": (now + timedelta(days=1)).isoformat(),
                    "checkOut": (now + timedelta(days=4)).isoformat(),
                    "guest": {"_id": "g1", "fullName": "Guest"},
                    "confirmationCode": "ABC123",
                }
            ],
        }
        payload = {"body": healthy}

        def handler(request: httpx.Request) -> httpx.Response:
            """Serve the listing document and the current payload.

            Args:
                request: Outgoing request.

            Returns:
                Mocked JSON response.
            """
            if request.url.path.startswith("/v1/listings/"):
                return httpx.Response(200, json={"_id": "proj_prop", "title": "X"})
            return httpx.Response(200, json=payload["body"])

        provider = self._provider(handler)
        sync = SyncService(
            async_session,
            calendar_cache=CalendarCache(ttl_seconds=0),
            session_factory=async_session_factory,
        )
        counts = await sync.sync_listing(listing, provider)
        assert counts["inserted"] == 1

        payload["body"] = default_projection
        # SyncService wraps the provider error; the point is that it
        # propagates as a failure instead of yielding an empty snapshot.
        with pytest.raises(SyncServiceError, match="default projection"):
            await sync.sync_listing(listing, provider)
        await provider.aclose()

        result = await async_session.execute(
            select(Booking).where(Booking.listing_id == listing.id)
        )
        bookings = list(result.scalars().all())
        assert len(bookings) == 1
        assert bookings[0].status == "confirmed"
