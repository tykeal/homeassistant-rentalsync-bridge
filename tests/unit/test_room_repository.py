# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for RoomRepository."""

import pytest
from src.models import Listing, Room


class TestRoomRepositoryGetByListingId:
    """Tests for get_by_listing_id method."""

    @pytest.mark.asyncio
    async def test_get_rooms_by_listing_id(self, async_session):
        """Test getting rooms for a listing."""
        from src.repositories.room_repository import RoomRepository

        listing = Listing(
            cloudbeds_id="rooms_listing_test",
            name="Rooms Listing Test",
            ical_url_slug="rooms-listing-test",
            enabled=True,
            sync_enabled=True,
            timezone="UTC",
        )
        async_session.add(listing)
        await async_session.flush()

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

        repo = RoomRepository(async_session)
        rooms = await repo.get_by_listing_id(listing.id)

        assert len(rooms) == 2
        assert room1 in rooms
        assert room2 in rooms

    @pytest.mark.asyncio
    async def test_get_rooms_empty_listing(self, async_session):
        """Test getting rooms for a listing with no rooms."""
        from src.repositories.room_repository import RoomRepository

        listing = Listing(
            cloudbeds_id="empty_rooms_test",
            name="Empty Rooms Test",
            ical_url_slug="empty-rooms-test",
            enabled=True,
            sync_enabled=True,
            timezone="UTC",
        )
        async_session.add(listing)
        await async_session.flush()

        repo = RoomRepository(async_session)
        rooms = await repo.get_by_listing_id(listing.id)

        assert len(rooms) == 0


class TestRoomRepositoryGetById:
    """Tests for get_by_id method."""

    @pytest.mark.asyncio
    async def test_get_room_by_id(self, async_session):
        """Test getting a room by ID."""
        from src.repositories.room_repository import RoomRepository

        listing = Listing(
            cloudbeds_id="room_by_id_test",
            name="Room By ID Test",
            ical_url_slug="room-by-id-test",
            enabled=True,
            sync_enabled=True,
            timezone="UTC",
        )
        async_session.add(listing)
        await async_session.flush()

        room = Room(
            listing_id=listing.id,
            cloudbeds_room_id="room_by_id",
            room_name="Room By ID",
            ical_url_slug="room-by-id",
            enabled=True,
        )
        async_session.add(room)
        await async_session.flush()

        repo = RoomRepository(async_session)
        found = await repo.get_by_id(room.id)

        assert found is not None
        assert found.id == room.id
        assert found.room_name == "Room By ID"

    @pytest.mark.asyncio
    async def test_get_room_by_id_not_found(self, async_session):
        """Test getting a non-existent room by ID."""
        from src.repositories.room_repository import RoomRepository

        repo = RoomRepository(async_session)
        found = await repo.get_by_id(99999)

        assert found is None


class TestRoomRepositoryGetBySlug:
    """Tests for get_by_slug method."""

    @pytest.mark.asyncio
    async def test_get_room_by_slug(self, async_session):
        """Test getting a room by listing slug and room slug."""
        from src.repositories.room_repository import RoomRepository

        listing = Listing(
            cloudbeds_id="room_slug_test",
            name="Room Slug Test",
            ical_url_slug="room-slug-test",
            enabled=True,
            sync_enabled=True,
            timezone="UTC",
        )
        async_session.add(listing)
        await async_session.flush()

        room = Room(
            listing_id=listing.id,
            cloudbeds_room_id="room_slug",
            room_name="Room Slug",
            ical_url_slug="the-room-slug",
            enabled=True,
        )
        async_session.add(room)
        await async_session.flush()

        repo = RoomRepository(async_session)
        found = await repo.get_by_slug("room-slug-test", "the-room-slug")

        assert found is not None
        assert found.id == room.id
        assert found.ical_url_slug == "the-room-slug"

    @pytest.mark.asyncio
    async def test_get_room_by_slug_not_found(self, async_session):
        """Test getting a non-existent room by slug."""
        from src.repositories.room_repository import RoomRepository

        listing = Listing(
            cloudbeds_id="no_room_slug_test",
            name="No Room Slug Test",
            ical_url_slug="no-room-slug-test",
            enabled=True,
            sync_enabled=True,
            timezone="UTC",
        )
        async_session.add(listing)
        await async_session.flush()

        repo = RoomRepository(async_session)
        found = await repo.get_by_slug("no-room-slug-test", "nonexistent")

        assert found is None

    @pytest.mark.asyncio
    async def test_get_room_by_slug_wrong_listing(self, async_session):
        """Test getting a room with wrong listing slug returns None."""
        from src.repositories.room_repository import RoomRepository

        listing = Listing(
            cloudbeds_id="wrong_listing_test",
            name="Wrong Listing Test",
            ical_url_slug="wrong-listing-test",
            enabled=True,
            sync_enabled=True,
            timezone="UTC",
        )
        async_session.add(listing)
        await async_session.flush()

        room = Room(
            listing_id=listing.id,
            cloudbeds_room_id="room_wrong",
            room_name="Room Wrong",
            ical_url_slug="room-wrong",
            enabled=True,
        )
        async_session.add(room)
        await async_session.flush()

        repo = RoomRepository(async_session)
        found = await repo.get_by_slug("different-listing", "room-wrong")

        assert found is None


class TestRoomRepositoryUpsert:
    """Tests for upsert_room method."""

    @pytest.mark.asyncio
    async def test_upsert_creates_new_room(self, async_session):
        """Test upserting a new room creates it."""
        from src.repositories.room_repository import RoomRepository

        listing = Listing(
            cloudbeds_id="upsert_create_test",
            name="Upsert Create Test",
            ical_url_slug="upsert-create-test",
            enabled=True,
            sync_enabled=True,
            timezone="UTC",
        )
        async_session.add(listing)
        await async_session.flush()

        repo = RoomRepository(async_session)
        room = await repo.upsert_room(
            listing_id=listing.id,
            cloudbeds_room_id="new_room",
            room_name="New Room",
            room_type_name="Standard",
        )

        assert room.id is not None
        assert room.cloudbeds_room_id == "new_room"
        assert room.room_name == "New Room"
        assert room.room_type_name == "Standard"
        assert room.enabled is True
        assert room.ical_url_slug == "new-room"

    @pytest.mark.asyncio
    async def test_upsert_updates_existing_room(self, async_session):
        """Test upserting an existing room updates it."""
        from src.repositories.room_repository import RoomRepository

        listing = Listing(
            cloudbeds_id="upsert_update_test",
            name="Upsert Update Test",
            ical_url_slug="upsert-update-test",
            enabled=True,
            sync_enabled=True,
            timezone="UTC",
        )
        async_session.add(listing)
        await async_session.flush()

        existing_room = Room(
            listing_id=listing.id,
            cloudbeds_room_id="existing_room",
            room_name="Old Name",
            room_type_name="Old Type",
            ical_url_slug="old-slug",
            enabled=True,
        )
        async_session.add(existing_room)
        await async_session.flush()

        repo = RoomRepository(async_session)
        room = await repo.upsert_room(
            listing_id=listing.id,
            cloudbeds_room_id="existing_room",
            room_name="New Name",
            room_type_name="New Type",
        )

        assert room.id == existing_room.id
        assert room.room_name == "New Name"
        assert room.room_type_name == "New Type"
        # Slug should not change on update
        assert room.ical_url_slug == "old-slug"


class TestRoomRepositoryToggleEnabled:
    """Tests for toggle_room_enabled method."""

    @pytest.mark.asyncio
    async def test_toggle_room_enabled(self, async_session):
        """Test toggling room enabled status."""
        from src.repositories.room_repository import RoomRepository

        listing = Listing(
            cloudbeds_id="toggle_test",
            name="Toggle Test",
            ical_url_slug="toggle-test",
            enabled=True,
            sync_enabled=True,
            timezone="UTC",
        )
        async_session.add(listing)
        await async_session.flush()

        room = Room(
            listing_id=listing.id,
            cloudbeds_room_id="toggle_room",
            room_name="Toggle Room",
            ical_url_slug="toggle-room",
            enabled=True,
        )
        async_session.add(room)
        await async_session.flush()

        repo = RoomRepository(async_session)

        # Disable
        updated = await repo.toggle_room_enabled(room.id, enabled=False)
        assert updated is not None
        assert updated.enabled is False

        # Enable
        updated = await repo.toggle_room_enabled(room.id, enabled=True)
        assert updated is not None
        assert updated.enabled is True

    @pytest.mark.asyncio
    async def test_toggle_room_enabled_not_found(self, async_session):
        """Test toggling non-existent room returns None."""
        from src.repositories.room_repository import RoomRepository

        repo = RoomRepository(async_session)
        result = await repo.toggle_room_enabled(99999, enabled=False)

        assert result is None


class TestRoomRepositoryUpdateSlug:
    """Tests for update_slug method."""

    @pytest.mark.asyncio
    async def test_update_room_slug(self, async_session):
        """Test updating room slug."""
        from src.repositories.room_repository import RoomRepository

        listing = Listing(
            cloudbeds_id="slug_update_test",
            name="Slug Update Test",
            ical_url_slug="slug-update-test",
            enabled=True,
            sync_enabled=True,
            timezone="UTC",
        )
        async_session.add(listing)
        await async_session.flush()

        room = Room(
            listing_id=listing.id,
            cloudbeds_room_id="slug_room",
            room_name="Slug Room",
            ical_url_slug="old-room-slug",
            enabled=True,
        )
        async_session.add(room)
        await async_session.flush()

        repo = RoomRepository(async_session)
        updated = await repo.update_slug(room.id, "new-room-slug")

        assert updated is not None
        assert updated.ical_url_slug == "new-room-slug"

    @pytest.mark.asyncio
    async def test_update_room_slug_not_found(self, async_session):
        """Test updating slug of non-existent room returns None."""
        from src.repositories.room_repository import RoomRepository

        repo = RoomRepository(async_session)
        result = await repo.update_slug(99999, "new-slug")

        assert result is None


class TestSlugGeneration:
    """Tests for slug generation utility."""

    def test_slugify_basic(self):
        """Test basic slug generation."""
        from src.repositories.room_repository import RoomRepository

        assert RoomRepository._slugify("Room 101") == "room-101"
        assert RoomRepository._slugify("Deluxe Suite") == "deluxe-suite"

    def test_slugify_special_characters(self):
        """Test slug generation with special characters."""
        from src.repositories.room_repository import RoomRepository

        assert RoomRepository._slugify("Room #1 (VIP)") == "room-1-vip"
        assert RoomRepository._slugify("Suite & Spa") == "suite-spa"

    def test_slugify_multiple_spaces(self):
        """Test slug generation with multiple spaces."""
        from src.repositories.room_repository import RoomRepository

        assert RoomRepository._slugify("Room   101") == "room-101"

    def test_slugify_leading_trailing_spaces(self):
        """Test slug generation with leading/trailing spaces."""
        from src.repositories.room_repository import RoomRepository

        assert RoomRepository._slugify("  Room 101  ") == "room-101"

    def test_slugify_empty_string(self):
        """Test slug generation with empty string."""
        from src.repositories.room_repository import RoomRepository

        assert RoomRepository._slugify("") == "room"
        assert RoomRepository._slugify("   ") == "room"

    def test_slugify_truncation(self):
        """Test slug generation truncates long names."""
        from src.repositories.room_repository import RoomRepository

        long_name = "A" * 200
        slug = RoomRepository._slugify(long_name)
        assert len(slug) <= 100


class TestRoomRepositoryGetEnabledByListingId:
    """Tests for get_enabled_by_listing_id method."""

    @pytest.mark.asyncio
    async def test_get_enabled_rooms_only(self, async_session):
        """Test getting only enabled rooms for a listing."""
        from src.repositories.room_repository import RoomRepository

        listing = Listing(
            cloudbeds_id="enabled_rooms_test",
            name="Enabled Rooms Test",
            ical_url_slug="enabled-rooms-test",
            enabled=True,
            sync_enabled=True,
            timezone="UTC",
        )
        async_session.add(listing)
        await async_session.flush()

        enabled_room = Room(
            listing_id=listing.id,
            cloudbeds_room_id="enabled_room",
            room_name="Enabled Room",
            ical_url_slug="enabled-room",
            enabled=True,
        )
        disabled_room = Room(
            listing_id=listing.id,
            cloudbeds_room_id="disabled_room",
            room_name="Disabled Room",
            ical_url_slug="disabled-room",
            enabled=False,
        )
        async_session.add(enabled_room)
        async_session.add(disabled_room)
        await async_session.flush()

        repo = RoomRepository(async_session)
        rooms = await repo.get_enabled_by_listing_id(listing.id)

        assert len(rooms) == 1
        assert rooms[0].id == enabled_room.id
