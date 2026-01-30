# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Integration tests for iCal API endpoint."""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from src.database import Base, get_db
from src.main import create_app
from src.models.booking import Booking
from src.models.custom_field import CustomField
from src.models.listing import Listing


@pytest.fixture
async def test_engine():
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
async def test_session(test_engine) -> AsyncGenerator[AsyncSession]:
    """Create test database session."""
    session_factory = async_sessionmaker(
        test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )
    async with session_factory() as session:
        yield session


@pytest.fixture
async def test_app(test_engine) -> AsyncGenerator:
    """Create test app with overridden DB dependency."""
    app = create_app()
    session_factory = async_sessionmaker(
        test_engine,
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


@pytest.mark.asyncio
async def test_get_ical_success(test_app, test_session):
    """Test successful iCal feed retrieval."""
    # Create test listing
    listing = Listing(
        cloudbeds_id="test_prop",
        name="Test Property",
        ical_url_slug="test-property-ical",
        enabled=True,
        sync_enabled=True,
        timezone="America/New_York",
    )
    test_session.add(listing)
    await test_session.commit()
    await test_session.refresh(listing)

    # Create test booking
    booking = Booking(
        listing_id=listing.id,
        cloudbeds_booking_id="BK12345",
        guest_name="Test Guest",
        guest_phone_last4="5678",
        check_in_date=datetime(2026, 3, 1, 14, 0, tzinfo=UTC),
        check_out_date=datetime(2026, 3, 5, 11, 0, tzinfo=UTC),
        status="confirmed",
    )
    test_session.add(booking)
    await test_session.commit()

    async with AsyncClient(
        transport=ASGITransport(app=test_app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/ical/test-property-ical.ics",
            headers={"Authorization": "Bearer test_token"},
        )

    assert response.status_code == 200
    assert response.headers["content-type"] == "text/calendar; charset=utf-8"
    assert "BEGIN:VCALENDAR" in response.text
    assert "Test Guest" in response.text


@pytest.mark.asyncio
async def test_get_ical_not_found(test_app, test_session):
    """Test 404 for unknown slug."""
    async with AsyncClient(
        transport=ASGITransport(app=test_app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/ical/unknown-slug.ics",
            headers={"Authorization": "Bearer test_token"},
        )

    assert response.status_code == 404
    assert response.json()["detail"] == "Listing not found"


@pytest.mark.asyncio
async def test_get_ical_disabled_listing(test_app, test_session):
    """Test 404 for disabled listing."""
    # Create disabled listing
    listing = Listing(
        cloudbeds_id="disabled_prop",
        name="Disabled Property",
        ical_url_slug="disabled-property",
        enabled=False,
        sync_enabled=True,
    )
    test_session.add(listing)
    await test_session.commit()

    async with AsyncClient(
        transport=ASGITransport(app=test_app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/ical/disabled-property.ics",
            headers={"Authorization": "Bearer test_token"},
        )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_ical_with_custom_fields(test_app, test_session):
    """Test iCal with custom fields."""
    # Create listing
    listing = Listing(
        cloudbeds_id="custom_prop",
        name="Custom Property",
        ical_url_slug="custom-property-test",
        enabled=True,
        sync_enabled=True,
    )
    test_session.add(listing)
    await test_session.commit()
    await test_session.refresh(listing)

    # Create custom field
    custom_field = CustomField(
        listing_id=listing.id,
        field_name="booking_notes",
        display_label="Notes",
        enabled=True,
        sort_order=0,
    )
    test_session.add(custom_field)

    # Create booking with custom data
    booking = Booking(
        listing_id=listing.id,
        cloudbeds_booking_id="BK99999",
        guest_name="VIP Guest",
        check_in_date=datetime(2026, 4, 1, tzinfo=UTC),
        check_out_date=datetime(2026, 4, 3, tzinfo=UTC),
        status="confirmed",
        custom_data={"booking_notes": "Special requests noted"},
    )
    test_session.add(booking)
    await test_session.commit()

    async with AsyncClient(
        transport=ASGITransport(app=test_app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/ical/custom-property-test.ics",
            headers={"Authorization": "Bearer test_token"},
        )

    assert response.status_code == 200
    assert "VIP Guest" in response.text
    assert "Special requests noted" in response.text


@pytest.mark.asyncio
async def test_get_ical_content_disposition_header(test_app, test_session):
    """Test Content-Disposition header is set correctly."""
    listing = Listing(
        cloudbeds_id="header_prop",
        name="Header Test Property",
        ical_url_slug="header-test",
        enabled=True,
        sync_enabled=True,
    )
    test_session.add(listing)
    await test_session.commit()

    async with AsyncClient(
        transport=ASGITransport(app=test_app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/ical/header-test.ics",
            headers={"Authorization": "Bearer test_token"},
        )

    assert response.status_code == 200
    assert 'attachment; filename="header-test.ics"' in response.headers.get(
        "content-disposition", ""
    )


@pytest.mark.asyncio
async def test_get_ical_empty_bookings(test_app, test_session):
    """Test iCal with no bookings."""
    listing = Listing(
        cloudbeds_id="empty_prop",
        name="Empty Property",
        ical_url_slug="empty-property",
        enabled=True,
        sync_enabled=True,
    )
    test_session.add(listing)
    await test_session.commit()

    async with AsyncClient(
        transport=ASGITransport(app=test_app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/ical/empty-property.ics",
            headers={"Authorization": "Bearer test_token"},
        )

    assert response.status_code == 200
    assert "BEGIN:VCALENDAR" in response.text
    # Should have no VEVENT blocks
    assert "BEGIN:VEVENT" not in response.text
