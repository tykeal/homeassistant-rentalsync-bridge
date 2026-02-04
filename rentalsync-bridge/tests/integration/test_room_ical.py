# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Integration tests for room-level iCal endpoint."""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from icalendar import Calendar
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from src.database import Base, get_db
from src.main import create_app
from src.models.booking import Booking
from src.models.listing import Listing
from src.models.room import Room


@pytest.fixture
async def ical_engine():
    """Create test database engine with FK support."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def set_sqlite_pragma(dbapi_conn, connection_record):
        """Enable SQLite foreign key constraints."""
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def ical_session(ical_engine) -> AsyncGenerator[AsyncSession]:
    """Create test database session."""
    session_factory = async_sessionmaker(
        ical_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with session_factory() as session:
        yield session


@pytest.fixture
async def ical_app(ical_engine) -> AsyncGenerator:
    """Create test app with overridden DB dependency."""
    app = create_app()
    session_factory = async_sessionmaker(
        ical_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async def override_get_db() -> AsyncGenerator[AsyncSession]:
        """Override database session."""
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = override_get_db
    yield app
    app.dependency_overrides.clear()


@pytest.fixture
async def listing_with_rooms(ical_session: AsyncSession) -> Listing:
    """Create a listing with multiple rooms for testing."""
    listing = Listing(
        cloudbeds_id="test-property-123",
        name="Test Multi-Room Property",
        ical_url_slug="test-property",
        timezone="America/Los_Angeles",
        enabled=True,
    )
    ical_session.add(listing)
    await ical_session.commit()
    await ical_session.refresh(listing)
    return listing


@pytest.fixture
async def room1(ical_session: AsyncSession, listing_with_rooms: Listing) -> Room:
    """Create first test room."""
    room = Room(
        listing_id=listing_with_rooms.id,
        cloudbeds_room_id="room-001",
        room_name="Ocean View Suite",
        room_type_name="Suite",
        ical_url_slug="ocean-view-suite",
        enabled=True,
    )
    ical_session.add(room)
    await ical_session.commit()
    await ical_session.refresh(room)
    return room


@pytest.fixture
async def room2(ical_session: AsyncSession, listing_with_rooms: Listing) -> Room:
    """Create second test room."""
    room = Room(
        listing_id=listing_with_rooms.id,
        cloudbeds_room_id="room-002",
        room_name="Mountain View Deluxe",
        room_type_name="Deluxe",
        ical_url_slug="mountain-view-deluxe",
        enabled=True,
    )
    ical_session.add(room)
    await ical_session.commit()
    await ical_session.refresh(room)
    return room


@pytest.fixture
async def bookings_for_rooms(
    ical_session: AsyncSession, listing_with_rooms: Listing, room1: Room, room2: Room
) -> list[Booking]:
    """Create bookings for different rooms."""
    bookings = [
        # Room 1 bookings
        Booking(
            listing_id=listing_with_rooms.id,
            room_id=room1.id,
            cloudbeds_booking_id="booking-room1-1",
            guest_name="Alice Johnson",
            guest_phone_last4="1234",
            check_in_date=datetime(2026, 3, 1, 15, 0, tzinfo=UTC),
            check_out_date=datetime(2026, 3, 5, 11, 0, tzinfo=UTC),
            status="confirmed",
        ),
        Booking(
            listing_id=listing_with_rooms.id,
            room_id=room1.id,
            cloudbeds_booking_id="booking-room1-2",
            guest_name="Bob Smith",
            guest_phone_last4="5678",
            check_in_date=datetime(2026, 3, 10, 15, 0, tzinfo=UTC),
            check_out_date=datetime(2026, 3, 15, 11, 0, tzinfo=UTC),
            status="confirmed",
        ),
        # Room 2 bookings
        Booking(
            listing_id=listing_with_rooms.id,
            room_id=room2.id,
            cloudbeds_booking_id="booking-room2-1",
            guest_name="Charlie Brown",
            guest_phone_last4="9012",
            check_in_date=datetime(2026, 3, 3, 15, 0, tzinfo=UTC),
            check_out_date=datetime(2026, 3, 8, 11, 0, tzinfo=UTC),
            status="confirmed",
        ),
    ]

    for booking in bookings:
        ical_session.add(booking)

    await ical_session.commit()
    return bookings


class TestRoomICalEndpoint:
    """Test room-level iCal endpoint functionality."""

    @pytest.mark.asyncio
    async def test_get_room_ical_feed_success(
        self,
        ical_app,
        listing_with_rooms: Listing,
        room1: Room,
        bookings_for_rooms: list[Booking],
    ) -> None:
        """Test successful retrieval of room-level iCal feed."""
        async with AsyncClient(
            transport=ASGITransport(app=ical_app), base_url="http://test"
        ) as client:
            response = await client.get(
                f"/ical/{listing_with_rooms.ical_url_slug}/{room1.ical_url_slug}.ics"
            )

        assert response.status_code == 200
        assert response.headers["content-type"] == "text/calendar; charset=utf-8"
        assert (
            response.headers["content-disposition"]
            == f'attachment; filename="{room1.ical_url_slug}.ics"'
        )

        # Parse iCal content
        cal = Calendar.from_ical(response.content)
        events = [c for c in cal.walk() if c.name == "VEVENT"]

        # Should have exactly 2 events for room1
        assert len(events) == 2

        # Verify event details
        summaries = [str(event.get("SUMMARY")) for event in events]
        assert "Alice Johnson" in summaries
        assert "Bob Smith" in summaries
        assert "Charlie Brown" not in summaries  # Room 2 booking

    @pytest.mark.asyncio
    async def test_get_room_ical_feed_different_room(
        self,
        ical_app,
        listing_with_rooms: Listing,
        room2: Room,
        bookings_for_rooms: list[Booking],
    ) -> None:
        """Test that different rooms return different bookings."""
        async with AsyncClient(
            transport=ASGITransport(app=ical_app), base_url="http://test"
        ) as client:
            response = await client.get(
                f"/ical/{listing_with_rooms.ical_url_slug}/{room2.ical_url_slug}.ics"
            )

        assert response.status_code == 200

        # Parse iCal content
        cal = Calendar.from_ical(response.content)
        events = [c for c in cal.walk() if c.name == "VEVENT"]

        # Should have exactly 1 event for room2
        assert len(events) == 1

        # Verify event details
        summaries = [str(event.get("SUMMARY")) for event in events]
        assert "Charlie Brown" in summaries
        assert "Alice Johnson" not in summaries  # Room 1 booking
        assert "Bob Smith" not in summaries  # Room 1 booking

    @pytest.mark.asyncio
    async def test_get_room_ical_feed_no_bookings(
        self,
        ical_app,
        ical_session: AsyncSession,
        listing_with_rooms: Listing,
    ) -> None:
        """Test room iCal feed with a room that has no bookings."""
        # Create a room with no bookings
        empty_room = Room(
            listing_id=listing_with_rooms.id,
            cloudbeds_room_id="room-empty",
            room_name="Empty Room",
            ical_url_slug="empty-room",
            enabled=True,
        )
        ical_session.add(empty_room)
        await ical_session.commit()
        await ical_session.refresh(empty_room)

        async with AsyncClient(
            transport=ASGITransport(app=ical_app), base_url="http://test"
        ) as client:
            response = await client.get(
                f"/ical/{listing_with_rooms.ical_url_slug}/{empty_room.ical_url_slug}.ics"
            )

        assert response.status_code == 200

        # Parse iCal content
        cal = Calendar.from_ical(response.content)
        events = [c for c in cal.walk() if c.name == "VEVENT"]

        # Should have no events
        assert len(events) == 0

    @pytest.mark.asyncio
    async def test_get_room_ical_feed_invalid_listing_slug(
        self, ical_app, room1: Room
    ) -> None:
        """Test 404 error for invalid listing slug."""
        async with AsyncClient(
            transport=ASGITransport(app=ical_app), base_url="http://test"
        ) as client:
            response = await client.get(
                f"/ical/invalid-listing/{room1.ical_url_slug}.ics"
            )

        assert response.status_code == 404
        assert response.json()["detail"] == "Room not found"

    @pytest.mark.asyncio
    async def test_get_room_ical_feed_invalid_room_slug(
        self, ical_app, listing_with_rooms: Listing
    ) -> None:
        """Test 404 error for invalid room slug."""
        async with AsyncClient(
            transport=ASGITransport(app=ical_app), base_url="http://test"
        ) as client:
            response = await client.get(
                f"/ical/{listing_with_rooms.ical_url_slug}/invalid-room.ics"
            )

        assert response.status_code == 404
        assert response.json()["detail"] == "Room not found"

    @pytest.mark.asyncio
    async def test_get_room_ical_feed_disabled_room(
        self,
        ical_app,
        ical_session: AsyncSession,
        listing_with_rooms: Listing,
    ) -> None:
        """Test 404 error for disabled room."""
        # Create a disabled room
        disabled_room = Room(
            listing_id=listing_with_rooms.id,
            cloudbeds_room_id="room-disabled",
            room_name="Disabled Room",
            ical_url_slug="disabled-room",
            enabled=False,
        )
        ical_session.add(disabled_room)
        await ical_session.commit()
        await ical_session.refresh(disabled_room)

        async with AsyncClient(
            transport=ASGITransport(app=ical_app), base_url="http://test"
        ) as client:
            response = await client.get(
                f"/ical/{listing_with_rooms.ical_url_slug}/{disabled_room.ical_url_slug}.ics"
            )

        assert response.status_code == 404
        assert response.json()["detail"] == "Room not found"

    @pytest.mark.asyncio
    async def test_get_room_ical_feed_disabled_listing(
        self,
        ical_app,
        ical_session: AsyncSession,
    ) -> None:
        """Test 404 error for room in disabled listing."""
        # Create a disabled listing with a room
        disabled_listing = Listing(
            cloudbeds_id="disabled-property",
            name="Disabled Property",
            ical_url_slug="disabled-property",
            timezone="America/Los_Angeles",
            enabled=False,
        )
        ical_session.add(disabled_listing)
        await ical_session.commit()
        await ical_session.refresh(disabled_listing)

        room = Room(
            listing_id=disabled_listing.id,
            cloudbeds_room_id="room-in-disabled",
            room_name="Room in Disabled Listing",
            ical_url_slug="room-in-disabled",
            enabled=True,
        )
        ical_session.add(room)
        await ical_session.commit()
        await ical_session.refresh(room)

        async with AsyncClient(
            transport=ASGITransport(app=ical_app), base_url="http://test"
        ) as client:
            response = await client.get(
                f"/ical/{disabled_listing.ical_url_slug}/{room.ical_url_slug}.ics"
            )

        assert response.status_code == 404
        assert response.json()["detail"] == "Room not found"

    @pytest.mark.asyncio
    async def test_room_ical_calendar_metadata(
        self,
        ical_app,
        listing_with_rooms: Listing,
        room1: Room,
        bookings_for_rooms: list[Booking],
    ) -> None:
        """Test that calendar metadata includes listing information."""
        async with AsyncClient(
            transport=ASGITransport(app=ical_app), base_url="http://test"
        ) as client:
            response = await client.get(
                f"/ical/{listing_with_rooms.ical_url_slug}/{room1.ical_url_slug}.ics"
            )

        assert response.status_code == 200

        # Parse iCal
        cal = Calendar.from_ical(response.content)

        # Verify calendar metadata
        assert str(cal.get("X-WR-CALNAME")) == listing_with_rooms.name
        assert str(cal.get("X-WR-TIMEZONE")) == listing_with_rooms.timezone
        assert str(cal.get("VERSION")) == "2.0"
        assert str(cal.get("PRODID")) == "-//RentalSync Bridge//rentalsync-bridge//EN"

    @pytest.mark.asyncio
    async def test_room_ical_event_details(
        self,
        ical_app,
        listing_with_rooms: Listing,
        room1: Room,
        bookings_for_rooms: list[Booking],
    ) -> None:
        """Test that event details are correctly formatted."""
        async with AsyncClient(
            transport=ASGITransport(app=ical_app), base_url="http://test"
        ) as client:
            response = await client.get(
                f"/ical/{listing_with_rooms.ical_url_slug}/{room1.ical_url_slug}.ics"
            )

        assert response.status_code == 200

        # Parse iCal
        cal = Calendar.from_ical(response.content)
        events = [c for c in cal.walk() if c.name == "VEVENT"]

        # Get first event
        event = events[0]

        # Verify event structure
        assert event.get("UID") is not None
        assert event.get("DTSTART") is not None
        assert event.get("DTEND") is not None
        assert event.get("SUMMARY") is not None
        assert str(event.get("STATUS")) == "CONFIRMED"
        assert str(event.get("TRANSP")) == "OPAQUE"

        # Verify description includes booking ID
        description = str(event.get("DESCRIPTION", ""))
        assert "Booking ID:" in description


class TestRoomICalCaching:
    """Test caching behavior for room-level iCal feeds."""

    @pytest.mark.asyncio
    async def test_room_ical_uses_cache(
        self,
        ical_app,
        listing_with_rooms: Listing,
        room1: Room,
        bookings_for_rooms: list[Booking],
    ) -> None:
        """Test that subsequent requests use cached iCal."""
        url = f"/ical/{listing_with_rooms.ical_url_slug}/{room1.ical_url_slug}.ics"

        async with AsyncClient(
            transport=ASGITransport(app=ical_app), base_url="http://test"
        ) as client:
            # First request - generates and caches
            response1 = await client.get(url)
            assert response1.status_code == 200
            content1 = response1.content

            # Second request - should return cached result
            response2 = await client.get(url)
            assert response2.status_code == 200
            content2 = response2.content

        # Content should be identical (from cache)
        assert content1 == content2

    @pytest.mark.asyncio
    async def test_room_ical_separate_cache_per_room(
        self,
        ical_app,
        listing_with_rooms: Listing,
        room1: Room,
        room2: Room,
        bookings_for_rooms: list[Booking],
    ) -> None:
        """Test that different rooms have separate cache entries."""
        url1 = f"/ical/{listing_with_rooms.ical_url_slug}/{room1.ical_url_slug}.ics"
        url2 = f"/ical/{listing_with_rooms.ical_url_slug}/{room2.ical_url_slug}.ics"

        async with AsyncClient(
            transport=ASGITransport(app=ical_app), base_url="http://test"
        ) as client:
            # Request both room calendars
            response1 = await client.get(url1)
            response2 = await client.get(url2)

        assert response1.status_code == 200
        assert response2.status_code == 200

        # Content should be different (different bookings)
        assert response1.content != response2.content

        # Parse and verify
        cal1 = Calendar.from_ical(response1.content)
        cal2 = Calendar.from_ical(response2.content)

        events1 = [c for c in cal1.walk() if c.name == "VEVENT"]
        events2 = [c for c in cal2.walk() if c.name == "VEVENT"]

        # Room 1 has 2 bookings, Room 2 has 1
        assert len(events1) == 2
        assert len(events2) == 1


class TestLegacyICalEndpoint:
    """Test legacy iCal endpoint returns helpful migration message."""

    @pytest.mark.asyncio
    async def test_legacy_endpoint_returns_410_gone(
        self,
        ical_app,
    ) -> None:
        """Test that old-format iCal URL returns 410 with helpful message."""
        async with AsyncClient(
            transport=ASGITransport(app=ical_app), base_url="http://test"
        ) as client:
            response = await client.get("/ical/old-listing-slug.ics")

        assert response.status_code == 410
        detail = response.json()["detail"]
        assert "iCal URL format has changed" in detail
        assert "room-level URLs" in detail
        assert "/ical/old-listing-slug/{room-slug}.ics" in detail
