# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Integration tests for listings API endpoints."""

from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from src.database import Base, get_db
from src.main import create_app
from src.models.listing import Listing


@pytest.fixture
async def listings_engine():
    """Create test database engine."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def listings_session(listings_engine) -> AsyncGenerator[AsyncSession]:
    """Create test database session."""
    session_factory = async_sessionmaker(
        listings_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with session_factory() as session:
        yield session


@pytest.fixture
async def listings_app(listings_engine) -> AsyncGenerator:
    """Create test app with overridden DB dependency."""
    app = create_app()
    session_factory = async_sessionmaker(
        listings_engine,
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


class TestListListings:
    """Tests for GET /api/listings endpoint."""

    @pytest.mark.asyncio
    async def test_list_empty(self, listings_app):
        """Test listing when no properties exist."""
        async with AsyncClient(
            transport=ASGITransport(app=listings_app), base_url="http://test"
        ) as client:
            response = await client.get(
                "/api/listings",
                headers={"Authorization": "Bearer test"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["listings"] == []
        assert data["total"] == 0

    @pytest.mark.asyncio
    async def test_list_with_listings(self, listings_app, listings_session):
        """Test listing with properties."""
        listing1 = Listing(
            cloudbeds_id="PROP1",
            name="Beach House",
            ical_url_slug="beach-house",
            enabled=True,
            sync_enabled=True,
        )
        listing2 = Listing(
            cloudbeds_id="PROP2",
            name="Mountain Cabin",
            ical_url_slug="mountain-cabin",
            enabled=False,
            sync_enabled=False,
        )
        listings_session.add_all([listing1, listing2])
        await listings_session.commit()

        async with AsyncClient(
            transport=ASGITransport(app=listings_app), base_url="http://test"
        ) as client:
            response = await client.get(
                "/api/listings",
                headers={"Authorization": "Bearer test"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        assert len(data["listings"]) == 2


class TestGetListing:
    """Tests for GET /api/listings/{id} endpoint."""

    @pytest.mark.asyncio
    async def test_get_existing(self, listings_app, listings_session):
        """Test getting existing listing."""
        listing = Listing(
            cloudbeds_id="PROP1",
            name="Test Property",
            ical_url_slug="test-property",
            enabled=True,
            sync_enabled=True,
            timezone="America/New_York",
        )
        listings_session.add(listing)
        await listings_session.commit()
        await listings_session.refresh(listing)

        async with AsyncClient(
            transport=ASGITransport(app=listings_app), base_url="http://test"
        ) as client:
            response = await client.get(
                f"/api/listings/{listing.id}",
                headers={"Authorization": "Bearer test"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["cloudbeds_id"] == "PROP1"
        assert data["name"] == "Test Property"
        assert data["timezone"] == "America/New_York"

    @pytest.mark.asyncio
    async def test_get_not_found(self, listings_app):
        """Test getting non-existent listing."""
        async with AsyncClient(
            transport=ASGITransport(app=listings_app), base_url="http://test"
        ) as client:
            response = await client.get(
                "/api/listings/999",
                headers={"Authorization": "Bearer test"},
            )

        assert response.status_code == 404


class TestEnableListing:
    """Tests for POST /api/listings/{id}/enable endpoint."""

    @pytest.mark.asyncio
    async def test_enable_listing(self, listings_app, listings_session):
        """Test enabling a listing."""
        listing = Listing(
            cloudbeds_id="PROP1",
            name="Test Property",
            ical_url_slug="test-property-slug",
            enabled=False,
            sync_enabled=False,
        )
        listings_session.add(listing)
        await listings_session.commit()
        await listings_session.refresh(listing)

        async with AsyncClient(
            transport=ASGITransport(app=listings_app), base_url="http://test"
        ) as client:
            response = await client.post(
                f"/api/listings/{listing.id}/enable",
                headers={"Authorization": "Bearer test"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "/ical/" in data["ical_url"]
        assert ".ics" in data["ical_url"]

    @pytest.mark.asyncio
    async def test_enable_not_found(self, listings_app):
        """Test enabling non-existent listing."""
        async with AsyncClient(
            transport=ASGITransport(app=listings_app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/listings/999/enable",
                headers={"Authorization": "Bearer test"},
            )

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_enable_max_listings_exceeded(self, listings_app, listings_session):
        """Test enabling fails when max listings reached."""
        # Create 50 enabled listings (the maximum)
        for i in range(50):
            listing = Listing(
                cloudbeds_id=f"PROP{i}",
                name=f"Property {i}",
                ical_url_slug=f"property-{i}",
                enabled=True,
                sync_enabled=True,
            )
            listings_session.add(listing)
        await listings_session.commit()

        # Create one more disabled listing
        new_listing = Listing(
            cloudbeds_id="PROP_NEW",
            name="New Property",
            ical_url_slug="new-property",
            enabled=False,
            sync_enabled=False,
        )
        listings_session.add(new_listing)
        await listings_session.commit()
        await listings_session.refresh(new_listing)

        # Try to enable - should fail
        async with AsyncClient(
            transport=ASGITransport(app=listings_app), base_url="http://test"
        ) as client:
            response = await client.post(
                f"/api/listings/{new_listing.id}/enable",
                headers={"Authorization": "Bearer test"},
            )

        assert response.status_code == 400
        assert "Maximum" in response.json()["detail"]


class TestUpdateListing:
    """Tests for PUT /api/listings/{id} endpoint."""

    @pytest.mark.asyncio
    async def test_update_listing(self, listings_app, listings_session):
        """Test updating a listing."""
        listing = Listing(
            cloudbeds_id="PROP1",
            name="Old Name",
            ical_url_slug="old-slug",
            enabled=False,
            sync_enabled=False,
        )
        listings_session.add(listing)
        await listings_session.commit()
        await listings_session.refresh(listing)

        async with AsyncClient(
            transport=ASGITransport(app=listings_app), base_url="http://test"
        ) as client:
            response = await client.put(
                f"/api/listings/{listing.id}",
                headers={"Authorization": "Bearer test"},
                json={
                    "name": "New Name",
                    "enabled": True,
                    "timezone": "America/Los_Angeles",
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "New Name"
        assert data["enabled"] is True
        assert data["timezone"] == "America/Los_Angeles"

    @pytest.mark.asyncio
    async def test_update_not_found(self, listings_app):
        """Test updating non-existent listing."""
        async with AsyncClient(
            transport=ASGITransport(app=listings_app), base_url="http://test"
        ) as client:
            response = await client.put(
                "/api/listings/999",
                headers={"Authorization": "Bearer test"},
                json={"name": "Test"},
            )

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_update_duplicate_slug(self, listings_app, listings_session):
        """Test updating with duplicate slug fails."""
        listing1 = Listing(
            cloudbeds_id="PROP1",
            name="Property 1",
            ical_url_slug="slug-one",
            enabled=True,
            sync_enabled=True,
        )
        listing2 = Listing(
            cloudbeds_id="PROP2",
            name="Property 2",
            ical_url_slug="slug-two",
            enabled=True,
            sync_enabled=True,
        )
        listings_session.add_all([listing1, listing2])
        await listings_session.commit()
        await listings_session.refresh(listing2)

        async with AsyncClient(
            transport=ASGITransport(app=listings_app), base_url="http://test"
        ) as client:
            response = await client.put(
                f"/api/listings/{listing2.id}",
                headers={"Authorization": "Bearer test"},
                json={"ical_url_slug": "slug-one"},  # Duplicate
            )

        assert response.status_code == 400
        assert "already in use" in response.json()["detail"]


class TestListingsDisplayForAdminUI:
    """Tests for admin UI listing display functionality."""

    @pytest.mark.asyncio
    async def test_list_returns_all_listings_with_enabled_state(
        self, listings_app, listings_session
    ):
        """Test that listing endpoint returns all listings with enabled/disabled state.

        T062: Verifies admin UI can display all listings regardless of enabled state.
        """
        # Create mix of enabled and disabled listings
        listing1 = Listing(
            cloudbeds_id="PROP_A",
            name="Enabled Property A",
            ical_url_slug="enabled-a",
            enabled=True,
            sync_enabled=True,
            timezone="America/New_York",
        )
        listing2 = Listing(
            cloudbeds_id="PROP_B",
            name="Disabled Property B",
            ical_url_slug="disabled-b",  # Required even when disabled
            enabled=False,
            sync_enabled=False,
        )
        listing3 = Listing(
            cloudbeds_id="PROP_C",
            name="Enabled Property C",
            ical_url_slug="enabled-c",
            enabled=True,
            sync_enabled=True,
            timezone="America/Los_Angeles",
        )
        listings_session.add_all([listing1, listing2, listing3])
        await listings_session.commit()

        async with AsyncClient(
            transport=ASGITransport(app=listings_app), base_url="http://test"
        ) as client:
            response = await client.get(
                "/api/listings",
                headers={"Authorization": "Bearer test"},
            )

        assert response.status_code == 200
        data = response.json()

        # Should return all 3 listings
        assert data["total"] == 3
        assert len(data["listings"]) == 3

        # Verify each listing has enabled state for toggle display
        enabled_states = {
            item["cloudbeds_id"]: item["enabled"] for item in data["listings"]
        }
        assert enabled_states["PROP_A"] is True
        assert enabled_states["PROP_B"] is False
        assert enabled_states["PROP_C"] is True

        # Verify iCal URLs are present for enabled listings
        urls = {
            item["cloudbeds_id"]: item.get("ical_url_slug") for item in data["listings"]
        }
        assert urls["PROP_A"] == "enabled-a"
        assert urls["PROP_B"] == "disabled-b"  # Has slug but not enabled
        assert urls["PROP_C"] == "enabled-c"
