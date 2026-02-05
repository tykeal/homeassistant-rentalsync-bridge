# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for Room model."""
# mypy: disable-error-code="attr-defined"
# Tests are written before implementation - mypy errors expected

from datetime import datetime

import pytest
from src.models import Listing


class TestRoom:
    """Tests for Room model."""

    def test_create_room(self):
        """Test creating a room with all required fields."""
        # Import here to allow test to fail if model doesn't exist
        from src.models import Room

        room = Room(
            listing_id=1,
            cloudbeds_room_id="room_123",
            room_name="Room 201",
            room_type_name="Deluxe Suite",
            ical_url_slug="room-201",
            enabled=True,
        )

        assert room.listing_id == 1
        assert room.cloudbeds_room_id == "room_123"
        assert room.room_name == "Room 201"
        assert room.room_type_name == "Deluxe Suite"
        assert room.ical_url_slug == "room-201"
        assert room.enabled is True

    @pytest.mark.asyncio
    async def test_room_default_enabled(self, async_session):
        """Test room is enabled by default when persisted."""
        from src.models import Room

        listing = Listing(
            cloudbeds_id="default_enabled_test",
            name="Default Enabled Test",
            ical_url_slug="default-enabled-test",
            enabled=True,
            sync_enabled=True,
            timezone="UTC",
        )
        async_session.add(listing)
        await async_session.flush()

        room = Room(
            listing_id=listing.id,
            cloudbeds_room_id="room_456",
            room_name="Room 102",
            room_type_name="Standard",
            ical_url_slug="room-102",
            # Not setting enabled - should default to True
        )
        async_session.add(room)
        await async_session.flush()

        assert room.enabled is True

    def test_room_optional_room_type(self):
        """Test room_type_name is optional."""
        from src.models import Room

        room = Room(
            listing_id=1,
            cloudbeds_room_id="room_789",
            room_name="Room 303",
            ical_url_slug="room-303",
        )

        assert room.room_type_name is None

    def test_repr(self):
        """Test string representation."""
        from src.models import Room

        room = Room(
            listing_id=1,
            cloudbeds_room_id="room_123",
            room_name="Room 201",
            room_type_name="Suite",
            ical_url_slug="room-201",
        )
        room.id = 1

        assert "Room" in repr(room)
        assert "Room 201" in repr(room)


class TestRoomListingRelationship:
    """Tests for Room-Listing relationship."""

    @pytest.mark.asyncio
    async def test_room_belongs_to_listing(self, async_session):
        """Test room has a listing relationship."""
        from src.models import Room

        # Create listing first
        listing = Listing(
            cloudbeds_id="room_rel_test",
            name="Room Relationship Test",
            ical_url_slug="room-rel-test",
            enabled=True,
            sync_enabled=True,
            timezone="UTC",
        )
        async_session.add(listing)
        await async_session.flush()

        # Create room linked to listing
        room = Room(
            listing_id=listing.id,
            cloudbeds_room_id="room_rel_123",
            room_name="Room 101",
            room_type_name="Standard",
            ical_url_slug="room-101",
            enabled=True,
        )
        async_session.add(room)
        await async_session.flush()

        assert room.id is not None
        assert room.listing_id == listing.id
        assert room.listing == listing

    @pytest.mark.asyncio
    async def test_listing_has_rooms(self, async_session):
        """Test listing has rooms relationship."""
        from src.models import Room

        # Create listing
        listing = Listing(
            cloudbeds_id="listing_rooms_test",
            name="Listing Rooms Test",
            ical_url_slug="listing-rooms-test",
            enabled=True,
            sync_enabled=True,
            timezone="UTC",
        )
        async_session.add(listing)
        await async_session.flush()

        # Create multiple rooms
        room1 = Room(
            listing_id=listing.id,
            cloudbeds_room_id="room_1",
            room_name="Room 1",
            ical_url_slug="room-1",
            enabled=True,
        )
        room2 = Room(
            listing_id=listing.id,
            cloudbeds_room_id="room_2",
            room_name="Room 2",
            ical_url_slug="room-2",
            enabled=True,
        )
        async_session.add(room1)
        async_session.add(room2)
        await async_session.flush()

        # Refresh listing to load relationship
        await async_session.refresh(listing)

        assert len(listing.rooms) == 2
        assert room1 in listing.rooms
        assert room2 in listing.rooms

    @pytest.mark.asyncio
    async def test_room_cascade_delete(self, async_session):
        """Test rooms are deleted when listing is deleted."""
        from src.models import Room

        # Create listing with room
        listing = Listing(
            cloudbeds_id="cascade_test",
            name="Cascade Test",
            ical_url_slug="cascade-test",
            enabled=True,
            sync_enabled=True,
            timezone="UTC",
        )
        async_session.add(listing)
        await async_session.flush()

        room = Room(
            listing_id=listing.id,
            cloudbeds_room_id="cascade_room",
            room_name="Cascade Room",
            ical_url_slug="cascade-room",
            enabled=True,
        )
        async_session.add(room)
        await async_session.flush()

        room_id = room.id

        # Delete listing
        await async_session.delete(listing)
        await async_session.flush()

        # Room should be deleted via cascade
        from sqlalchemy import select

        result = await async_session.execute(select(Room).where(Room.id == room_id))
        assert result.scalar_one_or_none() is None

    @pytest.mark.asyncio
    async def test_room_unique_slug_per_listing(self, async_session):
        """Test room slug must be unique within a listing."""
        from sqlalchemy.exc import IntegrityError
        from src.models import Room

        listing = Listing(
            cloudbeds_id="unique_slug_test",
            name="Unique Slug Test",
            ical_url_slug="unique-slug-test",
            enabled=True,
            sync_enabled=True,
            timezone="UTC",
        )
        async_session.add(listing)
        await async_session.flush()

        room1 = Room(
            listing_id=listing.id,
            cloudbeds_room_id="room_a",
            room_name="Room A",
            ical_url_slug="same-slug",
            enabled=True,
        )
        async_session.add(room1)
        await async_session.flush()

        room2 = Room(
            listing_id=listing.id,
            cloudbeds_room_id="room_b",
            room_name="Room B",
            ical_url_slug="same-slug",  # Same slug - should fail
            enabled=True,
        )
        async_session.add(room2)

        with pytest.raises(IntegrityError):
            await async_session.flush()


class TestRoomBookingRelationship:
    """Tests for Room-Booking relationship."""

    @pytest.mark.asyncio
    async def test_booking_can_have_room(self, async_session):
        """Test booking can be associated with a room."""
        from src.models import Booking, Room

        # Create listing
        listing = Listing(
            cloudbeds_id="booking_room_test",
            name="Booking Room Test",
            ical_url_slug="booking-room-test",
            enabled=True,
            sync_enabled=True,
            timezone="UTC",
        )
        async_session.add(listing)
        await async_session.flush()

        # Create room
        room = Room(
            listing_id=listing.id,
            cloudbeds_room_id="booking_room",
            room_name="Booking Room",
            ical_url_slug="booking-room",
            enabled=True,
        )
        async_session.add(room)
        await async_session.flush()

        # Create booking with room
        booking = Booking(
            listing_id=listing.id,
            room_id=room.id,
            cloudbeds_booking_id="BK_ROOM_123",
            guest_name="Room Guest",
            check_in_date=datetime(2026, 3, 1),
            check_out_date=datetime(2026, 3, 5),
            status="confirmed",
        )
        async_session.add(booking)
        await async_session.flush()

        assert booking.room_id == room.id
        assert booking.room == room

    @pytest.mark.asyncio
    async def test_booking_room_is_optional(self, async_session):
        """Test booking room_id is optional (nullable)."""
        from src.models import Booking

        listing = Listing(
            cloudbeds_id="no_room_test",
            name="No Room Test",
            ical_url_slug="no-room-test",
            enabled=True,
            sync_enabled=True,
            timezone="UTC",
        )
        async_session.add(listing)
        await async_session.flush()

        # Create booking without room
        booking = Booking(
            listing_id=listing.id,
            room_id=None,
            cloudbeds_booking_id="BK_NO_ROOM",
            guest_name="No Room Guest",
            check_in_date=datetime(2026, 4, 1),
            check_out_date=datetime(2026, 4, 3),
            status="confirmed",
        )
        async_session.add(booking)
        await async_session.flush()

        assert booking.id is not None
        assert booking.room_id is None
        assert booking.room is None

    @pytest.mark.asyncio
    async def test_room_has_bookings(self, async_session):
        """Test room has bookings relationship."""
        from src.models import Booking, Room

        listing = Listing(
            cloudbeds_id="room_bookings_test",
            name="Room Bookings Test",
            ical_url_slug="room-bookings-test",
            enabled=True,
            sync_enabled=True,
            timezone="UTC",
        )
        async_session.add(listing)
        await async_session.flush()

        room = Room(
            listing_id=listing.id,
            cloudbeds_room_id="multi_booking_room",
            room_name="Multi Booking Room",
            ical_url_slug="multi-booking-room",
            enabled=True,
        )
        async_session.add(room)
        await async_session.flush()

        # Create multiple bookings for the room
        booking1 = Booking(
            listing_id=listing.id,
            room_id=room.id,
            cloudbeds_booking_id="BK_MULTI_1",
            guest_name="Guest 1",
            check_in_date=datetime(2026, 5, 1),
            check_out_date=datetime(2026, 5, 3),
            status="confirmed",
        )
        booking2 = Booking(
            listing_id=listing.id,
            room_id=room.id,
            cloudbeds_booking_id="BK_MULTI_2",
            guest_name="Guest 2",
            check_in_date=datetime(2026, 5, 5),
            check_out_date=datetime(2026, 5, 7),
            status="confirmed",
        )
        async_session.add(booking1)
        async_session.add(booking2)
        await async_session.flush()

        await async_session.refresh(room)

        assert len(room.bookings) == 2
        assert booking1 in room.bookings
        assert booking2 in room.bookings

    @pytest.mark.asyncio
    async def test_room_deletion_sets_bookings_null(self, async_session):
        """Test deleting a room sets booking.room_id to NULL."""
        from sqlalchemy import select
        from src.models import Booking, Room

        listing = Listing(
            cloudbeds_id="room_delete_test",
            name="Room Delete Test",
            ical_url_slug="room-delete-test",
            enabled=True,
            sync_enabled=True,
            timezone="UTC",
        )
        async_session.add(listing)
        await async_session.flush()

        room = Room(
            listing_id=listing.id,
            cloudbeds_room_id="delete_room",
            room_name="Delete Room",
            ical_url_slug="delete-room",
            enabled=True,
        )
        async_session.add(room)
        await async_session.flush()

        booking = Booking(
            listing_id=listing.id,
            room_id=room.id,
            cloudbeds_booking_id="BK_DELETE_ROOM",
            guest_name="Delete Room Guest",
            check_in_date=datetime(2026, 6, 1),
            check_out_date=datetime(2026, 6, 3),
            status="confirmed",
        )
        async_session.add(booking)
        await async_session.flush()

        booking_id = booking.id
        room_id = room.id

        # Delete room - booking should remain with room_id=NULL
        await async_session.delete(room)
        await async_session.flush()

        # Expire cached booking to force reload from database
        async_session.expire(booking)

        # Verify room is deleted
        result = await async_session.execute(select(Room).where(Room.id == room_id))
        assert result.scalar_one_or_none() is None

        # Verify booking still exists with room_id=NULL
        result = await async_session.execute(
            select(Booking).where(Booking.id == booking_id)
        )
        preserved_booking = result.scalar_one()
        assert preserved_booking is not None
        assert preserved_booking.room_id is None
