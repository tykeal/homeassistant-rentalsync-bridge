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
            pms_id="PROP1",
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
            pms_id="PROP1",
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
            pms_id="PROP1",
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
            pms_id="PROP2",
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
            pms_id="PROP1",
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
            pms_id="PROP1",
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
        # Verify error message includes valid field names (may be truncated)
        assert "Must be one of:" in detail
        # Check for a field that appears early alphabetically
        assert "adults" in detail or "total" in detail

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
            pms_id="PROP1",
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


MONEY_FIELDS = ("money_totalPaid", "money_balanceDue", "money_currency")
MONEY_REASON = (
    "Guesty Reservations V3 no longer includes payment totals on reservations"
)


class TestRetiredFields:
    """``money_*`` fields are flagged, never removed (T056, FR-037/038)."""

    @pytest.fixture
    def reservations_api(self, monkeypatch):
        """Select the Guesty reservations API for one test."""
        from src.config import get_settings

        def select_api(value: str) -> None:
            monkeypatch.setenv("GUESTY_RESERVATIONS_API", value)
            get_settings.cache_clear()

        yield select_api
        monkeypatch.undo()
        get_settings.cache_clear()

    @staticmethod
    async def _listing(session, pms_type: str | None) -> Listing:
        """Persist a listing with every money field discovered and enabled."""
        from src.models.available_field import AvailableField
        from src.models.oauth_credential import OAuthCredential

        if pms_type is not None:
            credential = OAuthCredential(client_id="client", pms_type=pms_type)
            credential.client_secret = "secret"
            session.add(credential)
        listing = Listing(
            pms_id="PROP1",
            name="Test Property",
            ical_url_slug="test-property",
            enabled=True,
            sync_enabled=True,
        )
        session.add(listing)
        await session.commit()
        await session.refresh(listing)
        for n, key in enumerate((*MONEY_FIELDS, "source")):
            session.add(
                AvailableField(
                    listing_id=listing.id,
                    field_key=key,
                    display_name=key,
                    sample_value="1",
                )
            )
            session.add(
                CustomField(
                    listing_id=listing.id,
                    field_name=key,
                    display_label=f"Label {n}",
                    enabled=True,
                    sort_order=n,
                )
            )
        await session.commit()
        return listing

    @staticmethod
    async def _responses(app, listing_id: int) -> dict[str, list[dict]]:
        """GET both field endpoints and PUT the configuration back."""
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            base = f"/api/listings/{listing_id}"
            configured = (await client.get(f"{base}/custom-fields")).json()["fields"]
            available = (await client.get(f"{base}/available-custom-fields")).json()[
                "available_fields"
            ]
            put = await client.put(
                f"{base}/custom-fields",
                json={
                    "fields": [
                        {
                            "field_name": f["field_name"],
                            "display_label": f["display_label"],
                            "enabled": f["field_name"] != "money_currency",
                            "sort_order": f["sort_order"],
                        }
                        for f in configured
                    ]
                },
            )
            assert put.status_code == 200
        return {
            "configured": configured,
            "available": available,
            "put": put.json()["fields"],
        }

    @staticmethod
    def _flags(entries: list[dict], key: str) -> dict[str, tuple]:
        """Index each entry's retired flag and reason by field name."""
        return {e[key]: (e["retired"], e["retired_reason"]) for e in entries}

    @pytest.mark.asyncio
    async def test_flagged_in_v3_mode(
        self, fields_app, fields_session, reservations_api
    ):
        """GET, available fields and the PUT response all flag money_*."""
        reservations_api("v3")
        listing = await self._listing(fields_session, "guesty")

        responses = await self._responses(fields_app, listing.id)

        for name, key in (
            ("configured", "field_name"),
            ("available", "field_key"),
            ("put", "field_name"),
        ):
            flags = self._flags(responses[name], key)
            for field in MONEY_FIELDS:
                assert flags[field] == (True, MONEY_REASON), (name, field)
            assert flags["source"] == (False, None), name

    @pytest.mark.asyncio
    async def test_put_never_deletes_a_retired_field(
        self, fields_app, fields_session, reservations_api
    ):
        """A retired field is saved, disabled and kept like any other."""
        reservations_api("v3")
        listing = await self._listing(fields_session, "guesty")

        responses = await self._responses(fields_app, listing.id)

        put = {f["field_name"]: f for f in responses["put"]}
        assert set(put) == {*MONEY_FIELDS, "source"}
        assert put["money_currency"]["enabled"] is False
        assert put["money_totalPaid"]["enabled"] is True
        stored = (
            (
                await fields_session.execute(
                    select(CustomField.field_name).where(
                        CustomField.listing_id == listing.id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert set(stored) == {*MONEY_FIELDS, "source"}

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("api", "pms_type"),
        [("legacy", "guesty"), ("v3", "cloudbeds"), ("v3", None)],
        ids=["guesty-legacy", "cloudbeds", "no-credential"],
    )
    async def test_not_flagged_otherwise(
        self, fields_app, fields_session, reservations_api, api, pms_type
    ):
        """Legacy Guesty, Cloudbeds and an unconfigured PMS retire nothing."""
        reservations_api(api)
        listing = await self._listing(fields_session, pms_type)

        responses = await self._responses(fields_app, listing.id)

        for name, key in (
            ("configured", "field_name"),
            ("available", "field_key"),
            ("put", "field_name"),
        ):
            assert set(self._flags(responses[name], key).values()) == {(False, None)}, (
                name
            )
