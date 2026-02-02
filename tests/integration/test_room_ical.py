# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Integration tests for room-level iCal endpoint."""

from datetime import UTC, datetime

import pytest
from fastapi import status
from httpx import AsyncClient, Response
from icalendar import Calendar
from sqlalchemy.ext.asyncio import AsyncSession
from src.models.booking import Booking
from src.models.listing import Listing
from src.models.room import Room
from src.repositories.booking_repository import BookingRepository
from src.repositories.listing_repository import ListingRepository
from src.repositories.room_repository import RoomRepository


@pytest.fixture
async def listing_with_rooms(db: AsyncSession) -> Listing:
    """Create a listing with multiple rooms for testing."""
    listing = Listing(
        cloudbeds_id="test-property-123",
        name="Test Multi-Room Property",
        ical_url_slug="test-property",
        timezone="America/Los_Angeles",
        enabled=True,
    )
    listing_repo = ListingRepository(db)
    listing = await listing_repo.create(listing)
    await db.commit()
    await db.refresh(listing)
    return listing


@pytest.fixture
async def room1(db: AsyncSession, listing_with_rooms: Listing) -> Room:
    """Create first test room."""
    room_repo = RoomRepository(db)
    room = await room_repo.create_room(
        listing_id=listing_with_rooms.id,
        cloudbeds_room_id="room-001",
        room_name="Ocean View Suite",
        room_type_name="Suite",
    )
    await db.commit()
    await db.refresh(room)
    return room


@pytest.fixture
async def room2(db: AsyncSession, listing_with_rooms: Listing) -> Room:
    """Create second test room."""
    room_repo = RoomRepository(db)
    room = await room_repo.create_room(
        listing_id=listing_with_rooms.id,
        cloudbeds_room_id="room-002",
        room_name="Mountain View Deluxe",
        room_type_name="Deluxe",
    )
    await db.commit()
    await db.refresh(room)
    return room


@pytest.fixture
async def bookings_for_rooms(
    db: AsyncSession, listing_with_rooms: Listing, room1: Room, room2: Room
) -> list[Booking]:
    """Create bookings for different rooms."""
    booking_repo = BookingRepository(db)

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
        # Booking without room assignment
        Booking(
            listing_id=listing_with_rooms.id,
            room_id=None,
            cloudbeds_booking_id="booking-no-room",
            guest_name="David Lee",
            guest_phone_last4="3456",
            check_in_date=datetime(2026, 3, 20, 15, 0, tzinfo=UTC),
            check_out_date=datetime(2026, 3, 25, 11, 0, tzinfo=UTC),
            status="confirmed",
        ),
    ]

    created_bookings = []
    for booking in bookings:
        created = await booking_repo.create(booking)
        created_bookings.append(created)

    await db.commit()
    return created_bookings


class TestRoomICalEndpoint:
    """Test room-level iCal endpoint functionality."""

    @pytest.mark.asyncio
    async def test_get_room_ical_feed_success(
        self,
        client: AsyncClient,
        listing_with_rooms: Listing,
        room1: Room,
        bookings_for_rooms: list[Booking],
    ) -> None:
        """Test successful retrieval of room-level iCal feed."""
        response: Response = await client.get(
            f"/ical/{listing_with_rooms.ical_url_slug}/{room1.ical_url_slug}.ics"
        )

        assert response.status_code == status.HTTP_200_OK
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
        client: AsyncClient,
        listing_with_rooms: Listing,
        room2: Room,
        bookings_for_rooms: list[Booking],
    ) -> None:
        """Test that different rooms return different bookings."""
        response: Response = await client.get(
            f"/ical/{listing_with_rooms.ical_url_slug}/{room2.ical_url_slug}.ics"
        )

        assert response.status_code == status.HTTP_200_OK

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
        client: AsyncClient,
        listing_with_rooms: Listing,
        db: AsyncSession,
    ) -> None:
        """Test room iCal feed with a room that has no bookings."""
        # Create a room with no bookings
        room_repo = RoomRepository(db)
        empty_room = await room_repo.create_room(
            listing_id=listing_with_rooms.id,
            cloudbeds_room_id="room-empty",
            room_name="Empty Room",
        )
        await db.commit()
        await db.refresh(empty_room)

        response: Response = await client.get(
            f"/ical/{listing_with_rooms.ical_url_slug}/{empty_room.ical_url_slug}.ics"
        )

        assert response.status_code == status.HTTP_200_OK

        # Parse iCal content
        cal = Calendar.from_ical(response.content)
        events = [c for c in cal.walk() if c.name == "VEVENT"]

        # Should have no events
        assert len(events) == 0

    @pytest.mark.asyncio
    async def test_get_room_ical_feed_invalid_listing_slug(
        self, client: AsyncClient, room1: Room
    ) -> None:
        """Test 404 error for invalid listing slug."""
        response: Response = await client.get(
            f"/ical/invalid-listing/{room1.ical_url_slug}.ics"
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert response.json()["detail"] == "Room not found"

    @pytest.mark.asyncio
    async def test_get_room_ical_feed_invalid_room_slug(
        self, client: AsyncClient, listing_with_rooms: Listing
    ) -> None:
        """Test 404 error for invalid room slug."""
        response: Response = await client.get(
            f"/ical/{listing_with_rooms.ical_url_slug}/invalid-room.ics"
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert response.json()["detail"] == "Room not found"

    @pytest.mark.asyncio
    async def test_get_room_ical_feed_disabled_room(
        self,
        client: AsyncClient,
        listing_with_rooms: Listing,
        room1: Room,
        db: AsyncSession,
    ) -> None:
        """Test 404 error for disabled room."""
        # Disable the room
        room_repo = RoomRepository(db)
        await room_repo.toggle_room_enabled(room1.id, enabled=False)
        await db.commit()

        response: Response = await client.get(
            f"/ical/{listing_with_rooms.ical_url_slug}/{room1.ical_url_slug}.ics"
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert response.json()["detail"] == "Room not found"

    @pytest.mark.asyncio
    async def test_get_room_ical_feed_disabled_listing(
        self,
        client: AsyncClient,
        listing_with_rooms: Listing,
        room1: Room,
        db: AsyncSession,
    ) -> None:
        """Test 404 error for room in disabled listing."""
        # Disable the listing
        listing_with_rooms.enabled = False
        await db.commit()

        response: Response = await client.get(
            f"/ical/{listing_with_rooms.ical_url_slug}/{room1.ical_url_slug}.ics"
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert response.json()["detail"] == "Room not found"

    @pytest.mark.asyncio
    async def test_room_ical_calendar_metadata(
        self,
        client: AsyncClient,
        listing_with_rooms: Listing,
        room1: Room,
        bookings_for_rooms: list[Booking],
    ) -> None:
        """Test that calendar metadata includes listing information."""
        response: Response = await client.get(
            f"/ical/{listing_with_rooms.ical_url_slug}/{room1.ical_url_slug}.ics"
        )

        assert response.status_code == status.HTTP_200_OK

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
        client: AsyncClient,
        listing_with_rooms: Listing,
        room1: Room,
        bookings_for_rooms: list[Booking],
    ) -> None:
        """Test that event details are correctly formatted."""
        response: Response = await client.get(
            f"/ical/{listing_with_rooms.ical_url_slug}/{room1.ical_url_slug}.ics"
        )

        assert response.status_code == status.HTTP_200_OK

        # Parse iCal
        cal = Calendar.from_ical(response.content)
        events = [c for c in cal.walk() if c.name == "VEVENT"]

        # Get first event (Alice Johnson)
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
        client: AsyncClient,
        listing_with_rooms: Listing,
        room1: Room,
        bookings_for_rooms: list[Booking],
    ) -> None:
        """Test that subsequent requests use cached iCal."""
        url = f"/ical/{listing_with_rooms.ical_url_slug}/{room1.ical_url_slug}.ics"

        # First request - generates and caches
        response1: Response = await client.get(url)
        assert response1.status_code == status.HTTP_200_OK
        content1 = response1.content

        # Second request - should return cached result
        response2: Response = await client.get(url)
        assert response2.status_code == status.HTTP_200_OK
        content2 = response2.content

        # Content should be identical (from cache)
        assert content1 == content2

    @pytest.mark.asyncio
    async def test_room_ical_separate_cache_per_room(
        self,
        client: AsyncClient,
        listing_with_rooms: Listing,
        room1: Room,
        room2: Room,
        bookings_for_rooms: list[Booking],
    ) -> None:
        """Test that different rooms have separate cache entries."""
        url1 = f"/ical/{listing_with_rooms.ical_url_slug}/{room1.ical_url_slug}.ics"
        url2 = f"/ical/{listing_with_rooms.ical_url_slug}/{room2.ical_url_slug}.ics"

        # Request both room calendars
        response1: Response = await client.get(url1)
        response2: Response = await client.get(url2)

        assert response1.status_code == status.HTTP_200_OK
        assert response2.status_code == status.HTTP_200_OK

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
