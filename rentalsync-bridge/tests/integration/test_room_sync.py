# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Integration tests for room sync functionality (T017)."""

from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from src.database import Base, get_db
from src.main import create_app
from src.models.listing import Listing
from src.models.oauth_credential import OAuthCredential
from src.models.room import Room
from src.providers.base import PMSListing, PMSRoom


@pytest.fixture
async def room_sync_engine():
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
async def room_sync_session(room_sync_engine) -> AsyncGenerator[AsyncSession]:
    """Create test database session."""
    session_factory = async_sessionmaker(
        room_sync_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with session_factory() as session:
        # Verify FK is enabled
        result = await session.execute(text("PRAGMA foreign_keys"))
        fk_enabled = result.scalar()
        assert fk_enabled == 1, "Foreign keys not enabled"
        yield session


@pytest.fixture
async def room_sync_app(room_sync_engine) -> AsyncGenerator:
    """Create test app with overridden DB dependency."""
    app = create_app()
    session_factory = async_sessionmaker(
        room_sync_engine,
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


class TestSyncPropertiesWithRooms:
    """Tests for POST /api/listings/sync-properties endpoint with room sync."""

    @pytest.mark.asyncio
    async def test_sync_creates_rooms_for_property(
        self, room_sync_app, room_sync_session
    ):
        """Test that sync creates rooms for each property."""
        # Create OAuth credential with API key
        credential = OAuthCredential(
            client_id="test_client",
            client_secret="test_secret",
        )
        credential.api_key = "test_api_key"
        room_sync_session.add(credential)
        await room_sync_session.commit()

        with patch("src.api.listings._create_provider_for_credential") as mock_create:
            mock_provider = AsyncMock()
            mock_provider.get_listings = AsyncMock(
                return_value=[
                    PMSListing(
                        pms_id="PROP123",
                        name="Beach House",
                        timezone="America/New_York",
                    ),
                ]
            )
            mock_provider.get_rooms = AsyncMock(
                return_value=[
                    PMSRoom(
                        pms_room_id="ROOM001",
                        name="Suite 101",
                        room_type="Deluxe Suite",
                    ),
                    PMSRoom(
                        pms_room_id="ROOM002",
                        name="Suite 102",
                        room_type="Standard Room",
                    ),
                ]
            )
            mock_provider.aclose = AsyncMock()
            mock_create.return_value = mock_provider

            async with AsyncClient(
                transport=ASGITransport(app=room_sync_app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/api/listings/sync-properties",
                    headers={"Authorization": "Bearer test"},
                )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

        # Verify rooms were created
        from sqlalchemy import select

        result = await room_sync_session.execute(select(Room))
        rooms = result.scalars().all()
        assert len(rooms) == 2
        room_names = {r.room_name for r in rooms}
        assert "Suite 101" in room_names
        assert "Suite 102" in room_names

    @pytest.mark.asyncio
    async def test_sync_updates_existing_rooms(self, room_sync_app, room_sync_session):
        """Test that sync updates existing room data."""
        # Create existing listing and room
        listing = Listing(
            pms_id="PROP123",
            name="Beach House",
            ical_url_slug="beach-house",
            enabled=True,
            sync_enabled=True,
        )
        room_sync_session.add(listing)
        await room_sync_session.commit()
        await room_sync_session.refresh(listing)

        existing_room = Room(
            listing_id=listing.id,
            pms_room_id="ROOM001",
            room_name="Old Name",
            room_type_name="Old Type",
            ical_url_slug="old-name",
            enabled=True,
        )
        room_sync_session.add(existing_room)
        await room_sync_session.commit()

        # Create OAuth credential
        credential = OAuthCredential(
            client_id="test_client",
            client_secret="test_secret",
        )
        credential.api_key = "test_api_key"
        room_sync_session.add(credential)
        await room_sync_session.commit()

        with patch("src.api.listings._create_provider_for_credential") as mock_create:
            mock_provider = AsyncMock()
            mock_provider.get_listings = AsyncMock(
                return_value=[
                    PMSListing(
                        pms_id="PROP123",
                        name="Beach House",
                        timezone="America/New_York",
                    ),
                ]
            )
            mock_provider.get_rooms = AsyncMock(
                return_value=[
                    PMSRoom(
                        pms_room_id="ROOM001", name="New Name", room_type="New Type"
                    ),
                ]
            )
            mock_provider.aclose = AsyncMock()
            mock_create.return_value = mock_provider

            async with AsyncClient(
                transport=ASGITransport(app=room_sync_app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/api/listings/sync-properties",
                    headers={"Authorization": "Bearer test"},
                )

        assert response.status_code == 200

        # Verify room was updated
        await room_sync_session.refresh(existing_room)
        assert existing_room.room_name == "New Name"
        assert existing_room.room_type_name == "New Type"
        # Slug should remain unchanged
        assert existing_room.ical_url_slug == "old-name"

    @pytest.mark.asyncio
    async def test_sync_handles_empty_rooms(self, room_sync_app, room_sync_session):
        """Test that sync handles properties with no rooms."""
        # Create OAuth credential
        credential = OAuthCredential(
            client_id="test_client",
            client_secret="test_secret",
        )
        credential.api_key = "test_api_key"
        room_sync_session.add(credential)
        await room_sync_session.commit()

        with patch("src.api.listings._create_provider_for_credential") as mock_create:
            mock_provider = AsyncMock()
            mock_provider.get_listings = AsyncMock(
                return_value=[
                    PMSListing(
                        pms_id="PROP_EMPTY", name="Empty Property", timezone="UTC"
                    ),
                ]
            )
            mock_provider.get_rooms = AsyncMock(return_value=[])  # No rooms
            mock_provider.aclose = AsyncMock()
            mock_create.return_value = mock_provider

            async with AsyncClient(
                transport=ASGITransport(app=room_sync_app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/api/listings/sync-properties",
                    headers={"Authorization": "Bearer test"},
                )

        assert response.status_code == 200

        # Verify no rooms were created
        from sqlalchemy import select

        result = await room_sync_session.execute(select(Room))
        rooms = result.scalars().all()
        assert len(rooms) == 0

    @pytest.mark.asyncio
    async def test_sync_preserves_room_enabled_state(
        self, room_sync_app, room_sync_session
    ):
        """Test that sync preserves user's enabled/disabled setting."""
        # Create existing listing and disabled room
        listing = Listing(
            pms_id="PROP123",
            name="Beach House",
            ical_url_slug="beach-house",
            enabled=True,
            sync_enabled=True,
        )
        room_sync_session.add(listing)
        await room_sync_session.commit()
        await room_sync_session.refresh(listing)

        disabled_room = Room(
            listing_id=listing.id,
            pms_room_id="ROOM001",
            room_name="Room 1",
            ical_url_slug="room-1",
            enabled=False,  # User disabled this room
        )
        room_sync_session.add(disabled_room)
        await room_sync_session.commit()

        # Create OAuth credential
        credential = OAuthCredential(
            client_id="test_client",
            client_secret="test_secret",
        )
        credential.api_key = "test_api_key"
        room_sync_session.add(credential)
        await room_sync_session.commit()

        with patch("src.api.listings._create_provider_for_credential") as mock_create:
            mock_provider = AsyncMock()
            mock_provider.get_listings = AsyncMock(
                return_value=[
                    PMSListing(
                        pms_id="PROP123",
                        name="Beach House",
                        timezone="UTC",
                    ),
                ]
            )
            mock_provider.get_rooms = AsyncMock(
                return_value=[
                    PMSRoom(pms_room_id="ROOM001", name="Room 1 Updated"),
                ]
            )
            mock_provider.aclose = AsyncMock()
            mock_create.return_value = mock_provider

            async with AsyncClient(
                transport=ASGITransport(app=room_sync_app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/api/listings/sync-properties",
                    headers={"Authorization": "Bearer test"},
                )

        assert response.status_code == 200

        # Verify enabled state was preserved
        await room_sync_session.refresh(disabled_room)
        assert disabled_room.enabled is False
        assert disabled_room.room_name == "Room 1 Updated"


class TestRoomSyncResponse:
    """Tests for room sync response data."""

    @pytest.mark.asyncio
    async def test_sync_returns_room_counts(self, room_sync_app, room_sync_session):
        """Test that sync response includes room sync counts."""
        # Create OAuth credential
        credential = OAuthCredential(
            client_id="test_client",
            client_secret="test_secret",
        )
        credential.api_key = "test_api_key"
        room_sync_session.add(credential)
        await room_sync_session.commit()

        def get_rooms_for_property(property_id):
            """Return mock rooms based on property."""
            if property_id == "PROP1":
                return [
                    PMSRoom(pms_room_id="R1", name="Room 1"),
                    PMSRoom(pms_room_id="R2", name="Room 2"),
                ]
            return [PMSRoom(pms_room_id="R3", name="Room 3")]

        with patch("src.api.listings._create_provider_for_credential") as mock_create:
            mock_provider = AsyncMock()
            mock_provider.get_listings = AsyncMock(
                return_value=[
                    PMSListing(
                        pms_id="PROP1",
                        name="Property 1",
                        timezone="UTC",
                    ),
                    PMSListing(
                        pms_id="PROP2",
                        name="Property 2",
                        timezone="UTC",
                    ),
                ]
            )
            mock_provider.get_rooms = AsyncMock(side_effect=get_rooms_for_property)
            mock_provider.aclose = AsyncMock()
            mock_create.return_value = mock_provider

            async with AsyncClient(
                transport=ASGITransport(app=room_sync_app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/api/listings/sync-properties",
                    headers={"Authorization": "Bearer test"},
                )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        # Response should include rooms_created count
        assert "rooms_created" in data or "created" in data
