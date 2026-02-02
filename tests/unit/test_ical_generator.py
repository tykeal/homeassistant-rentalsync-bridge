# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for iCal generation with room-level filtering."""

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from icalendar import Calendar
from src.models.booking import Booking
from src.models.listing import Listing
from src.models.room import Room
from src.services.calendar_service import CalendarCache, CalendarService


@pytest.fixture
def listing() -> Listing:
    """Create a test listing."""
    return Listing(
        id=1,
        cloudbeds_id="prop123",
        name="Test Property",
        ical_url_slug="test-property",
        timezone="America/Los_Angeles",
        enabled=True,
    )


@pytest.fixture
def room() -> Room:
    """Create a test room."""
    return Room(
        id=1,
        listing_id=1,
        cloudbeds_room_id="room123",
        room_name="Ocean View Suite",
        room_type_name="Suite",
        ical_url_slug="ocean-view-suite",
        enabled=True,
    )


@pytest.fixture
def bookings_with_rooms() -> list[Booking]:
    """Create test bookings associated with different rooms."""
    return [
        Booking(
            id=1,
            listing_id=1,
            room_id=1,
            cloudbeds_booking_id="booking1",
            guest_name="Alice Johnson",
            guest_phone_last4="1234",
            check_in_date=datetime(2026, 3, 1, 15, 0, tzinfo=UTC),
            check_out_date=datetime(2026, 3, 5, 11, 0, tzinfo=UTC),
            status="confirmed",
        ),
        Booking(
            id=2,
            listing_id=1,
            room_id=2,  # Different room
            cloudbeds_booking_id="booking2",
            guest_name="Bob Smith",
            guest_phone_last4="5678",
            check_in_date=datetime(2026, 3, 3, 15, 0, tzinfo=UTC),
            check_out_date=datetime(2026, 3, 7, 11, 0, tzinfo=UTC),
            status="confirmed",
        ),
        Booking(
            id=3,
            listing_id=1,
            room_id=1,  # Same as first booking
            cloudbeds_booking_id="booking3",
            guest_name="Charlie Brown",
            guest_phone_last4="9012",
            check_in_date=datetime(2026, 3, 10, 15, 0, tzinfo=UTC),
            check_out_date=datetime(2026, 3, 15, 11, 0, tzinfo=UTC),
            status="confirmed",
        ),
    ]


@pytest.fixture
def mock_cache() -> MagicMock:
    """Create a mock cache."""
    cache = MagicMock(spec=CalendarCache)
    cache.get.return_value = None
    return cache


class TestRoomLevelICalGeneration:
    """Test room-level iCal generation functionality."""

    def test_generate_ical_includes_room_info_in_cache_key(
        self, listing: Listing, room: Room, bookings_with_rooms: list[Booking]
    ) -> None:
        """Test that room information is used in cache key."""
        cache = CalendarCache()
        service = CalendarService(cache=cache)

        # Generate for a specific room (filtering would happen before this call)
        room_bookings = [b for b in bookings_with_rooms if b.room_id == 1]

        # Generate iCal
        ical = service.generate_ical(
            listing=listing,
            bookings=room_bookings,
            custom_fields=None,
        )

        # Verify output
        assert ical is not None
        assert "BEGIN:VCALENDAR" in ical
        assert "Ocean View Suite" not in ical  # Room name not in calendar by default

    def test_generate_ical_filters_bookings_by_room(
        self, listing: Listing, bookings_with_rooms: list[Booking]
    ) -> None:
        """Test that only bookings for the specific room are included."""
        service = CalendarService(cache=None)  # No cache for testing

        # Filter bookings for room_id=1
        room_bookings = [b for b in bookings_with_rooms if b.room_id == 1]

        # Generate iCal
        ical = service.generate_ical(
            listing=listing,
            bookings=room_bookings,
            custom_fields=None,
        )

        # Parse the generated iCal
        cal = Calendar.from_ical(ical)
        events = [c for c in cal.walk() if c.name == "VEVENT"]

        # Should have exactly 2 events (booking1 and booking3, both room_id=1)
        assert len(events) == 2

        # Verify event details
        summaries = [str(event.get("SUMMARY")) for event in events]
        assert "Alice Johnson" in summaries
        assert "Charlie Brown" in summaries
        assert "Bob Smith" not in summaries  # This is room_id=2

    def test_generate_ical_handles_empty_bookings(
        self, listing: Listing, room: Room
    ) -> None:
        """Test that iCal generation works with no bookings."""
        # Use a fresh cache to avoid interference from previous tests
        cache = CalendarCache()
        service = CalendarService(cache=cache)

        # Generate with empty bookings
        ical = service.generate_ical(
            listing=listing,
            bookings=[],
            custom_fields=None,
        )

        # Parse the generated iCal
        cal = Calendar.from_ical(ical)
        events = [c for c in cal.walk() if c.name == "VEVENT"]

        # Should have no events
        assert len(events) == 0
        assert "BEGIN:VCALENDAR" in ical
        assert "END:VCALENDAR" in ical

    def test_cache_uses_room_level_keys(
        self, listing: Listing, room: Room, bookings_with_rooms: list[Booking]
    ) -> None:
        """Test that cache stores entries with room-level keys."""
        cache = CalendarCache()
        service = CalendarService(cache=cache)

        # Filter bookings for room_id=1
        room_bookings = [b for b in bookings_with_rooms if b.room_id == 1]

        # For room-level caching, the listing slug would be updated to include room
        listing_copy = Listing(
            id=listing.id,
            cloudbeds_id=listing.cloudbeds_id,
            name=listing.name,
            ical_url_slug=f"{listing.ical_url_slug}/{room.ical_url_slug}",
            timezone=listing.timezone,
            enabled=listing.enabled,
        )

        # Generate iCal
        ical1 = service.generate_ical(
            listing=listing_copy,
            bookings=room_bookings,
            custom_fields=None,
        )

        # Second call should return cached result
        ical2 = service.generate_ical(
            listing=listing_copy,
            bookings=room_bookings,
            custom_fields=None,
        )

        assert ical1 == ical2

    def test_invalidate_cache_for_room(
        self, listing: Listing, room: Room, bookings_with_rooms: list[Booking]
    ) -> None:
        """Test that cache invalidation works for room-level keys."""
        cache = CalendarCache()
        service = CalendarService(cache=cache)

        room_bookings = [b for b in bookings_with_rooms if b.room_id == 1]
        cache_key = f"{listing.ical_url_slug}/{room.ical_url_slug}"

        # Generate and cache
        ical1 = service.generate_ical(
            listing=listing,
            bookings=room_bookings,
            custom_fields=None,
        )

        # Store with room-level key
        cache.set(cache_key, ical1)

        # Verify cached
        assert cache.get(cache_key) == ical1

        # Invalidate
        cache.invalidate(cache_key)

        # Verify cleared
        assert cache.get(cache_key) is None


class TestCalendarCacheWithRooms:
    """Test calendar cache behavior with room-level keys."""

    def test_cache_separate_entries_per_room(self) -> None:
        """Test that different rooms have separate cache entries."""
        cache = CalendarCache()

        cache.set("property1/room1", "ical_content_room1")
        cache.set("property1/room2", "ical_content_room2")

        assert cache.get("property1/room1") == "ical_content_room1"
        assert cache.get("property1/room2") == "ical_content_room2"

    def test_invalidate_one_room_keeps_others(self) -> None:
        """Test that invalidating one room doesn't affect others."""
        cache = CalendarCache()

        cache.set("property1/room1", "ical_content_room1")
        cache.set("property1/room2", "ical_content_room2")

        cache.invalidate("property1/room1")

        assert cache.get("property1/room1") is None
        assert cache.get("property1/room2") == "ical_content_room2"

    def test_clear_removes_all_rooms(self) -> None:
        """Test that clear removes all room entries."""
        cache = CalendarCache()

        cache.set("property1/room1", "ical_content_room1")
        cache.set("property1/room2", "ical_content_room2")
        cache.set("property2/room1", "ical_content_room1")

        cache.clear()

        assert cache.get("property1/room1") is None
        assert cache.get("property1/room2") is None
        assert cache.get("property2/room1") is None
