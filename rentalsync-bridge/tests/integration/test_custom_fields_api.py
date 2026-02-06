# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Integration tests for custom fields API endpoints."""

from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from src.database import Base, get_db
from src.main import create_app
from src.models.custom_field import CustomField
from src.models.listing import Listing


@pytest.fixture
async def fields_engine():
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
async def fields_session(fields_engine) -> AsyncGenerator[AsyncSession]:
    """Create test database session."""
    session_factory = async_sessionmaker(
        fields_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with session_factory() as session:
        yield session


@pytest.fixture
async def fields_app(fields_engine) -> AsyncGenerator:
    """Create test app with overridden DB dependency."""
    app = create_app()
    session_factory = async_sessionmaker(
        fields_engine,
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


class TestGetCustomFields:
    """Tests for GET /api/listings/{id}/custom-fields endpoint."""

    @pytest.mark.asyncio
    async def test_get_empty_fields(self, fields_app, fields_session):
        """Test getting custom fields when none exist."""
        listing = Listing(
            cloudbeds_id="PROP1",
            name="Test Property",
            ical_url_slug="test-property",
            enabled=True,
            sync_enabled=True,
        )
        fields_session.add(listing)
        await fields_session.commit()
        await fields_session.refresh(listing)

        async with AsyncClient(
            transport=ASGITransport(app=fields_app), base_url="http://test"
        ) as client:
            response = await client.get(
                f"/api/listings/{listing.id}/custom-fields",
                headers={"Authorization": "Bearer test"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["fields"] == []
        assert data["listing_id"] == listing.id

    @pytest.mark.asyncio
    async def test_get_existing_fields(self, fields_app, fields_session):
        """Test getting existing custom fields."""
        listing = Listing(
            cloudbeds_id="PROP1",
            name="Test Property",
            ical_url_slug="test-property",
            enabled=True,
            sync_enabled=True,
        )
        fields_session.add(listing)
        await fields_session.commit()
        await fields_session.refresh(listing)

        field = CustomField(
            listing_id=listing.id,
            field_name="guestName",
            display_label="Guest Name",
            enabled=True,
            sort_order=0,
        )
        fields_session.add(field)
        await fields_session.commit()

        async with AsyncClient(
            transport=ASGITransport(app=fields_app), base_url="http://test"
        ) as client:
            response = await client.get(
                f"/api/listings/{listing.id}/custom-fields",
                headers={"Authorization": "Bearer test"},
            )

        assert response.status_code == 200
        data = response.json()
        assert len(data["fields"]) == 1
        assert data["fields"][0]["field_name"] == "guestName"
        assert data["fields"][0]["display_label"] == "Guest Name"

    @pytest.mark.asyncio
    async def test_get_fields_not_found(self, fields_app):
        """Test getting fields for non-existent listing."""
        async with AsyncClient(
            transport=ASGITransport(app=fields_app), base_url="http://test"
        ) as client:
            response = await client.get(
                "/api/listings/999/custom-fields",
                headers={"Authorization": "Bearer test"},
            )

        assert response.status_code == 404


class TestUpdateCustomFields:
    """Tests for PUT /api/listings/{id}/custom-fields endpoint."""

    @pytest.mark.asyncio
    async def test_create_new_fields_builtin(self, fields_app, fields_session):
        """Test creating new built-in custom fields."""
        listing = Listing(
            cloudbeds_id="PROP1",
            name="Test Property",
            ical_url_slug="test-property",
            enabled=True,
            sync_enabled=True,
        )
        fields_session.add(listing)
        await fields_session.commit()
        await fields_session.refresh(listing)

        async with AsyncClient(
            transport=ASGITransport(app=fields_app), base_url="http://test"
        ) as client:
            # Use built-in field (always available)
            response = await client.put(
                f"/api/listings/{listing.id}/custom-fields",
                headers={"Authorization": "Bearer test"},
                json={
                    "fields": [
                        {
                            "field_name": "guest_phone_last4",
                            "display_label": "Phone (Last 4)",
                            "enabled": True,
                        },
                    ]
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert len(data["fields"]) == 1
        assert data["fields"][0]["field_name"] == "guest_phone_last4"

    @pytest.mark.asyncio
    async def test_create_new_fields_discovered(self, fields_app, fields_session):
        """Test creating custom fields from discovered fields.

        Uses a field key not in DEFAULT_CLOUDBEDS_FIELDS to verify
        that discovery is actually required for the field to be valid.
        """
        from src.models.available_field import AvailableField

        listing = Listing(
            cloudbeds_id="PROP2",
            name="Test Property 2",
            ical_url_slug="test-property-2",
            enabled=True,
            sync_enabled=True,
        )
        fields_session.add(listing)
        await fields_session.commit()
        await fields_session.refresh(listing)
        listing_id = listing.id

        # First verify the field is rejected before discovery
        async with AsyncClient(
            transport=ASGITransport(app=fields_app), base_url="http://test"
        ) as client:
            response = await client.put(
                f"/api/listings/{listing_id}/custom-fields",
                headers={"Authorization": "Bearer test"},
                json={
                    "fields": [
                        {
                            "field_name": "myCustomApiField",
                            "display_label": "My Custom Field",
                            "enabled": True,
                        },
                    ]
                },
            )
        assert response.status_code == 400
        assert "Invalid field_name" in response.json()["detail"]

        # Now discover the field (simulates sync discovering it)
        avail_field = AvailableField(
            listing_id=listing_id,
            field_key="myCustomApiField",
            display_name="My Custom API Field",
        )
        fields_session.add(avail_field)
        await fields_session.commit()

        # Now it should succeed
        async with AsyncClient(
            transport=ASGITransport(app=fields_app), base_url="http://test"
        ) as client:
            response = await client.put(
                f"/api/listings/{listing_id}/custom-fields",
                headers={"Authorization": "Bearer test"},
                json={
                    "fields": [
                        {
                            "field_name": "myCustomApiField",
                            "display_label": "My Custom Field",
                            "enabled": True,
                        },
                    ]
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert len(data["fields"]) == 1
        assert data["fields"][0]["field_name"] == "myCustomApiField"

    @pytest.mark.asyncio
    async def test_update_existing_fields(self, fields_app, fields_session):
        """Test updating existing custom fields."""
        listing = Listing(
            cloudbeds_id="PROP1",
            name="Test Property",
            ical_url_slug="test-property",
            enabled=True,
            sync_enabled=True,
        )
        fields_session.add(listing)
        await fields_session.commit()
        await fields_session.refresh(listing)

        # Create an existing field using built-in field
        field = CustomField(
            listing_id=listing.id,
            field_name="guest_phone_last4",
            display_label="Phone",
            enabled=True,
            sort_order=0,
        )
        fields_session.add(field)
        await fields_session.commit()

        async with AsyncClient(
            transport=ASGITransport(app=fields_app), base_url="http://test"
        ) as client:
            response = await client.put(
                f"/api/listings/{listing.id}/custom-fields",
                headers={"Authorization": "Bearer test"},
                json={
                    "fields": [
                        {
                            "field_name": "guest_phone_last4",
                            "display_label": "Phone (Last 4 Digits) Updated",
                            "enabled": False,
                        },
                    ]
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert len(data["fields"]) == 1
        assert data["fields"][0]["display_label"] == "Phone (Last 4 Digits) Updated"
        assert data["fields"][0]["enabled"] is False

    @pytest.mark.asyncio
    async def test_update_fields_rejects_invalid_field_name(
        self, fields_app, fields_session
    ):
        """Test that invalid field names are rejected."""
        listing = Listing(
            cloudbeds_id="PROP1",
            name="Test Property",
            ical_url_slug="test-property",
            enabled=True,
            sync_enabled=True,
        )
        fields_session.add(listing)
        await fields_session.commit()
        await fields_session.refresh(listing)

        async with AsyncClient(
            transport=ASGITransport(app=fields_app), base_url="http://test"
        ) as client:
            response = await client.put(
                f"/api/listings/{listing.id}/custom-fields",
                headers={"Authorization": "Bearer test"},
                json={
                    "fields": [
                        {
                            "field_name": "invalid_field_name",
                            "display_label": "Invalid",
                            "enabled": True,
                        },
                    ]
                },
            )

        assert response.status_code == 400
        detail = response.json()["detail"]
        assert "Invalid field_name" in detail
        assert "invalid_field_name" in detail
        # Verify error message includes valid field names
        assert "Must be one of:" in detail
        assert "guest_phone_last4" in detail  # One of the valid fields

    @pytest.mark.asyncio
    async def test_update_fields_not_found(self, fields_app):
        """Test updating fields for non-existent listing."""
        async with AsyncClient(
            transport=ASGITransport(app=fields_app), base_url="http://test"
        ) as client:
            response = await client.put(
                "/api/listings/999/custom-fields",
                headers={"Authorization": "Bearer test"},
                json={"fields": []},
            )

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_field_by_omission(self, fields_app, fields_session):
        """Test that fields not in update request are deleted."""
        listing = Listing(
            cloudbeds_id="PROP1",
            name="Test Property",
            ical_url_slug="test-property",
            enabled=True,
            sync_enabled=True,
        )
        fields_session.add(listing)
        await fields_session.commit()
        await fields_session.refresh(listing)
        listing_id = listing.id  # Store ID before session changes

        # Create two existing fields
        field1 = CustomField(
            listing_id=listing_id,
            field_name="guest_phone_last4",
            display_label="Phone",
            enabled=True,
            sort_order=0,
        )
        field2 = CustomField(
            listing_id=listing_id,
            field_name="guestName",
            display_label="Guest",
            enabled=True,
            sort_order=1,
        )
        fields_session.add_all([field1, field2])
        await fields_session.commit()

        async with AsyncClient(
            transport=ASGITransport(app=fields_app), base_url="http://test"
        ) as client:
            # Only include one field in update - the other should be deleted
            response = await client.put(
                f"/api/listings/{listing_id}/custom-fields",
                headers={"Authorization": "Bearer test"},
                json={
                    "fields": [
                        {
                            "field_name": "guestName",
                            "display_label": "Guest Name",
                            "enabled": True,
                        },
                    ]
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert len(data["fields"]) == 1
        assert data["fields"][0]["field_name"] == "guestName"

        # Verify guest_phone_last4 was actually deleted from the database
        fields_session.expire_all()
        result = await fields_session.execute(
            select(CustomField).where(CustomField.listing_id == listing_id)
        )
        remaining_fields = result.scalars().all()
        remaining_names = [f.field_name for f in remaining_fields]
        assert "guest_phone_last4" not in remaining_names
        assert "guestName" in remaining_names
