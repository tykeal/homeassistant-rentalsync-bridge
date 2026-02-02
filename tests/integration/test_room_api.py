# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Integration tests for room API endpoints."""

from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from src.database import Base, get_db
from src.main import create_app
from src.models.listing import Listing
from src.models.room import Room


@pytest.fixture
async def rooms_engine():
    """Create test database engine."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def set_sqlite_pragma(dbapi_connection, _connection_record):
        """Enable SQLite FK constraints on each connection."""
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def rooms_session(rooms_engine) -> AsyncGenerator[AsyncSession]:
    """Create test database session."""
    session_factory = async_sessionmaker(
        rooms_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with session_factory() as session:
        yield session


@pytest.fixture
async def rooms_app(rooms_engine) -> AsyncGenerator:
    """Create test app with overridden DB dependency."""
    app = create_app()
    session_factory = async_sessionmaker(
        rooms_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async def override_get_db() -> AsyncGenerator[AsyncSession]:
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


class TestGetListingRooms:
    """Tests for GET /api/listings/{id}/rooms endpoint."""

    @pytest.mark.asyncio
    async def test_get_listing_rooms(self, rooms_app, rooms_session):
        """Test getting rooms for a listing."""
        listing = Listing(
            cloudbeds_id="PROP_ROOMS",
            name="Property With Rooms",
            ical_url_slug="property-with-rooms",
            enabled=True,
            sync_enabled=True,
            timezone="UTC",
        )
        rooms_session.add(listing)
        await rooms_session.flush()

        room1 = Room(
            listing_id=listing.id,
            cloudbeds_room_id="ROOM1",
            room_name="Room 101",
            room_type_name="Standard",
            ical_url_slug="room-101",
            enabled=True,
        )
        room2 = Room(
            listing_id=listing.id,
            cloudbeds_room_id="ROOM2",
            room_name="Room 102",
            room_type_name="Deluxe",
            ical_url_slug="room-102",
            enabled=False,
        )
        rooms_session.add(room1)
        rooms_session.add(room2)
        await rooms_session.commit()

        transport = ASGITransport(app=rooms_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(f"/api/listings/{listing.id}/rooms")

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        assert len(data["rooms"]) == 2

        room_names = [r["room_name"] for r in data["rooms"]]
        assert "Room 101" in room_names
        assert "Room 102" in room_names

    @pytest.mark.asyncio
    async def test_get_listing_rooms_empty(self, rooms_app, rooms_session):
        """Test getting rooms for a listing with no rooms."""
        listing = Listing(
            cloudbeds_id="PROP_NO_ROOMS",
            name="Property No Rooms",
            ical_url_slug="property-no-rooms",
            enabled=True,
            sync_enabled=True,
            timezone="UTC",
        )
        rooms_session.add(listing)
        await rooms_session.commit()

        transport = ASGITransport(app=rooms_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(f"/api/listings/{listing.id}/rooms")

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
        assert len(data["rooms"]) == 0

    @pytest.mark.asyncio
    async def test_get_listing_rooms_not_found(self, rooms_app, rooms_session):
        """Test getting rooms for a non-existent listing."""
        transport = ASGITransport(app=rooms_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/listings/99999/rooms")

        assert response.status_code == 404


class TestGetRoom:
    """Tests for GET /api/rooms/{id} endpoint."""

    @pytest.mark.asyncio
    async def test_get_room(self, rooms_app, rooms_session):
        """Test getting a single room."""
        listing = Listing(
            cloudbeds_id="PROP_GET_ROOM",
            name="Get Room Property",
            ical_url_slug="get-room-property",
            enabled=True,
            sync_enabled=True,
            timezone="UTC",
        )
        rooms_session.add(listing)
        await rooms_session.flush()

        room = Room(
            listing_id=listing.id,
            cloudbeds_room_id="GET_ROOM",
            room_name="Get Room",
            room_type_name="Suite",
            ical_url_slug="get-room",
            enabled=True,
        )
        rooms_session.add(room)
        await rooms_session.commit()

        transport = ASGITransport(app=rooms_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(f"/api/rooms/{room.id}")

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == room.id
        assert data["room_name"] == "Get Room"
        assert data["room_type_name"] == "Suite"
        assert data["ical_url_slug"] == "get-room"
        assert data["enabled"] is True

    @pytest.mark.asyncio
    async def test_get_room_not_found(self, rooms_app, rooms_session):
        """Test getting a non-existent room."""
        transport = ASGITransport(app=rooms_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/rooms/99999")

        assert response.status_code == 404


class TestPatchRoom:
    """Tests for PATCH /api/rooms/{id} endpoint."""

    @pytest.mark.asyncio
    async def test_patch_room_enabled(self, rooms_app, rooms_session):
        """Test updating room enabled status."""
        listing = Listing(
            cloudbeds_id="PROP_PATCH",
            name="Patch Property",
            ical_url_slug="patch-property",
            enabled=True,
            sync_enabled=True,
            timezone="UTC",
        )
        rooms_session.add(listing)
        await rooms_session.flush()

        room = Room(
            listing_id=listing.id,
            cloudbeds_room_id="PATCH_ROOM",
            room_name="Patch Room",
            ical_url_slug="patch-room",
            enabled=True,
        )
        rooms_session.add(room)
        await rooms_session.commit()

        transport = ASGITransport(app=rooms_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.patch(
                f"/api/rooms/{room.id}",
                json={"enabled": False},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["enabled"] is False

    @pytest.mark.asyncio
    async def test_patch_room_slug(self, rooms_app, rooms_session):
        """Test updating room slug."""
        listing = Listing(
            cloudbeds_id="PROP_SLUG_PATCH",
            name="Slug Patch Property",
            ical_url_slug="slug-patch-property",
            enabled=True,
            sync_enabled=True,
            timezone="UTC",
        )
        rooms_session.add(listing)
        await rooms_session.flush()

        room = Room(
            listing_id=listing.id,
            cloudbeds_room_id="SLUG_PATCH_ROOM",
            room_name="Slug Patch Room",
            ical_url_slug="old-slug",
            enabled=True,
        )
        rooms_session.add(room)
        await rooms_session.commit()

        transport = ASGITransport(app=rooms_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.patch(
                f"/api/rooms/{room.id}",
                json={"ical_url_slug": "new-slug"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["ical_url_slug"] == "new-slug"

    @pytest.mark.asyncio
    async def test_patch_room_not_found(self, rooms_app, rooms_session):
        """Test patching a non-existent room."""
        transport = ASGITransport(app=rooms_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.patch(
                "/api/rooms/99999",
                json={"enabled": False},
            )

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_patch_room_slug_conflict(self, rooms_app, rooms_session):
        """Test updating room slug to one that already exists."""
        listing = Listing(
            cloudbeds_id="PROP_SLUG_CONFLICT",
            name="Slug Conflict Property",
            ical_url_slug="slug-conflict-property",
            enabled=True,
            sync_enabled=True,
            timezone="UTC",
        )
        rooms_session.add(listing)
        await rooms_session.flush()

        room1 = Room(
            listing_id=listing.id,
            cloudbeds_room_id="CONFLICT_ROOM1",
            room_name="Conflict Room 1",
            ical_url_slug="taken-slug",
            enabled=True,
        )
        room2 = Room(
            listing_id=listing.id,
            cloudbeds_room_id="CONFLICT_ROOM2",
            room_name="Conflict Room 2",
            ical_url_slug="my-slug",
            enabled=True,
        )
        rooms_session.add(room1)
        rooms_session.add(room2)
        await rooms_session.commit()

        transport = ASGITransport(app=rooms_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.patch(
                f"/api/rooms/{room2.id}",
                json={"ical_url_slug": "taken-slug"},
            )

        assert response.status_code == 400
        assert "already in use" in response.json()["detail"]
