# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Edge case integration tests for Guesty PMS provider.

Covers: guest 404 fallback, listing with no rooms, token limit
exhaustion and deferral, paginated multi-page results, and cancelled
reservation handling.
"""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from src.database import Base
from src.models.booking import Booking
from src.providers.base import (
    PMSReservation,
    TokenRateLimitError,
)
from src.providers.guesty.auth import GuestyTokenManager
from src.providers.guesty.service import GuestyProvider
from src.services.calendar_service import CalendarCache
from src.services.sync_service import SyncService

from tests.conftest import make_listing


@pytest.fixture
async def db_engine():
    """Create async in-memory SQLite engine."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def set_sqlite_pragma(dbapi_conn, _rec):
        """Enable SQLite FK constraints."""
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
def session_factory(db_engine):
    """Create async session factory bound to test engine."""
    return async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture
async def session(session_factory) -> AsyncGenerator[AsyncSession]:
    """Yield an async database session for each test."""
    async with session_factory() as s:
        yield s


class TestGuest404Fallback:
    """When get_guest returns None, booking uses fallback name."""

    async def test_guest_not_found_uses_booking_id(self, session, session_factory):
        """Test guest 404 falls back to booking ID as event title."""
        listing = make_listing(
            pms_id="edge_prop_1",
            name="Edge Property",
            slug="edge-prop-1",
        )
        session.add(listing)
        await session.commit()
        await session.refresh(listing)

        now = datetime.now(UTC)
        mock_provider = AsyncMock()
        mock_provider.provider_type = "guesty"
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

        sync = SyncService(
            session,
            calendar_cache=CalendarCache(ttl_seconds=0),
            session_factory=session_factory,
        )
        counts = await sync.sync_listing(listing, mock_provider)
        assert counts["inserted"] == 1

        result = await session.execute(
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

    async def test_reservation_without_room_ids(self, session, session_factory):
        """Test reservation with no room IDs stores NULL room."""
        listing = make_listing(
            pms_id="no_rooms_prop",
            name="No Rooms Property",
            slug="no-rooms-prop",
        )
        session.add(listing)
        await session.commit()
        await session.refresh(listing)

        now = datetime.now(UTC)
        mock_provider = AsyncMock()
        mock_provider.provider_type = "guesty"
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

        sync = SyncService(
            session,
            calendar_cache=CalendarCache(ttl_seconds=0),
            session_factory=session_factory,
        )
        counts = await sync.sync_listing(listing, mock_provider)
        assert counts["inserted"] == 1

        result = await session.execute(
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
        tm._cached_token = None
        tm._cached_expires_at = None

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
        self, session, session_factory
    ):
        """Test missing reservation on re-sync is marked cancelled."""
        listing = make_listing(
            pms_id="cancel_prop",
            name="Cancel Property",
            slug="cancel-prop",
        )
        session.add(listing)
        await session.commit()
        await session.refresh(listing)

        now = datetime.now(UTC)

        # First sync: two bookings
        mock_provider = AsyncMock()
        mock_provider.provider_type = "guesty"
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

        sync = SyncService(
            session,
            calendar_cache=CalendarCache(ttl_seconds=0),
            session_factory=session_factory,
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

        result = await session.execute(
            select(Booking)
            .where(Booking.listing_id == listing.id)
            .where(Booking.status == "cancelled")
        )
        cancelled = list(result.scalars().all())
        assert len(cancelled) == 1
        assert cancelled[0].pms_booking_id == "CAN_BK002"

    async def test_explicitly_cancelled_status_persisted(
        self, session, session_factory
    ):
        """Test explicitly cancelled reservation persists cancelled status."""
        listing = make_listing(
            pms_id="explicit_cancel",
            name="Explicit Cancel Property",
            slug="explicit-cancel",
        )
        session.add(listing)
        await session.commit()
        await session.refresh(listing)

        now = datetime.now(UTC)
        mock_provider = AsyncMock()
        mock_provider.provider_type = "guesty"
        mock_provider.get_reservations = AsyncMock(
            return_value=[
                PMSReservation(
                    pms_booking_id="EX_CANCEL",
                    listing_pms_id="explicit_cancel",
                    guest_name="Cancelled Guest",
                    guest_id=None,
                    check_in=now + timedelta(days=10),
                    check_out=now + timedelta(days=13),
                    status="cancelled",
                    room_ids=(),
                    custom_data={},
                ),
            ]
        )
        mock_provider.get_guest = AsyncMock(return_value=None)

        sync = SyncService(
            session,
            calendar_cache=CalendarCache(ttl_seconds=0),
            session_factory=session_factory,
        )
        counts = await sync.sync_listing(listing, mock_provider)
        assert counts["inserted"] == 1

        result = await session.execute(
            select(Booking).where(Booking.listing_id == listing.id)
        )
        booking = result.scalars().first()
        assert booking is not None
        assert booking.status == "cancelled"
