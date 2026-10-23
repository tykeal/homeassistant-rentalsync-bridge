# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Integration tests for the withheld bookings API endpoint."""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from src.database import Base, get_db
from src.main import create_app
from src.models.booking import Booking
from src.models.listing import Listing


@pytest.fixture
async def withheld_engine():
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
async def withheld_session(withheld_engine) -> AsyncGenerator[AsyncSession]:
    """Create test database session."""
    session_factory = async_sessionmaker(
        withheld_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with session_factory() as session:
        yield session


@pytest.fixture
async def withheld_app(withheld_engine) -> AsyncGenerator:
    """Create test app with overridden DB dependency."""
    app = create_app()
    session_factory = async_sessionmaker(
        withheld_engine,
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


async def _seed(session: AsyncSession) -> Listing:
    """Create an enabled listing with one withheld and one normal booking."""
    listing = Listing(
        pms_id="PROP1",
        name="Beach House",
        ical_url_slug="beach-house",
        enabled=True,
        sync_enabled=True,
    )
    session.add(listing)
    await session.commit()
    await session.refresh(listing)

    session.add_all(
        [
            Booking(
                listing_id=listing.id,
                pms_booking_id="BK_UNKNOWN",
                guest_name="Real Guest",
                guest_phone_last4="1234",
                check_in_date=datetime.now(UTC) + timedelta(days=1),
                check_out_date=datetime.now(UTC) + timedelta(days=4),
                status="Weird-Provider-Status",
            ),
            Booking(
                listing_id=listing.id,
                pms_booking_id="BK_EMPTY",
                guest_name="Another Guest",
                guest_phone_last4="5678",
                check_in_date=datetime.now(UTC) + timedelta(days=2),
                check_out_date=datetime.now(UTC) + timedelta(days=6),
                status="",
            ),
            Booking(
                listing_id=listing.id,
                pms_booking_id="BK_DECLINED",
                check_in_date=datetime.now(UTC) + timedelta(days=1),
                check_out_date=datetime.now(UTC) + timedelta(days=3),
                status="declined",
            ),
            Booking(
                listing_id=listing.id,
                pms_booking_id="BK_CONFIRMED",
                check_in_date=datetime.now(UTC) + timedelta(days=1),
                check_out_date=datetime.now(UTC) + timedelta(days=3),
                status="confirmed",
            ),
        ]
    )
    await session.commit()
    return listing


class TestWithheldBookings:
    """Tests for GET /api/bookings/withheld."""

    @pytest.mark.asyncio
    async def test_returns_raw_status_verbatim(self, withheld_app, withheld_session):
        """Raw provider statuses, including the empty one, survive intact."""
        listing = await _seed(withheld_session)

        async with AsyncClient(
            transport=ASGITransport(app=withheld_app), base_url="http://test"
        ) as client:
            response = await client.get(
                "/api/bookings/withheld",
                headers={"Authorization": "******"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 2
        by_id = {b["pms_booking_id"]: b for b in data["bookings"]}
        assert set(by_id) == {"BK_UNKNOWN", "BK_EMPTY"}
        assert by_id["BK_UNKNOWN"]["status"] == "Weird-Provider-Status"
        assert by_id["BK_EMPTY"]["status"] == ""
        assert by_id["BK_UNKNOWN"]["listing_id"] == listing.id
        assert by_id["BK_UNKNOWN"]["listing_name"] == "Beach House"

    @pytest.mark.asyncio
    async def test_response_contains_no_guest_pii(self, withheld_app, withheld_session):
        """Privacy guarantee: no guest name or phone digits in the payload."""
        await _seed(withheld_session)

        async with AsyncClient(
            transport=ASGITransport(app=withheld_app), base_url="http://test"
        ) as client:
            response = await client.get(
                "/api/bookings/withheld",
                headers={"Authorization": "******"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["bookings"]
        for booking in data["bookings"]:
            assert "guest_name" not in booking
            assert "guest_phone_last4" not in booking
        body = response.text
        assert "Real Guest" not in body
        assert "1234" not in body

    @pytest.mark.asyncio
    async def test_excludes_disabled_listings(self, withheld_app, withheld_session):
        """Bookings on disabled listings are never exported, so not shown."""
        listing = Listing(
            pms_id="PROP2",
            name="Disabled House",
            ical_url_slug="disabled-house",
            enabled=False,
            sync_enabled=True,
        )
        withheld_session.add(listing)
        await withheld_session.commit()
        await withheld_session.refresh(listing)

        withheld_session.add(
            Booking(
                listing_id=listing.id,
                pms_booking_id="BK_HIDDEN",
                check_in_date=datetime.now(UTC) + timedelta(days=1),
                check_out_date=datetime.now(UTC) + timedelta(days=3),
                status="mystery",
            )
        )
        await withheld_session.commit()

        async with AsyncClient(
            transport=ASGITransport(app=withheld_app), base_url="http://test"
        ) as client:
            response = await client.get(
                "/api/bookings/withheld",
                headers={"Authorization": "******"},
            )

        assert response.status_code == 200
        assert response.json() == {"bookings": [], "count": 0}

    @pytest.mark.asyncio
    async def test_status_reports_withheld_count(self, withheld_app, withheld_session):
        """/api/status exposes a withheld count for the UI badge."""
        await _seed(withheld_session)

        async with AsyncClient(
            transport=ASGITransport(app=withheld_app), base_url="http://test"
        ) as client:
            response = await client.get(
                "/api/status",
                headers={"Authorization": "******"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["bookings_count"] == 4
        assert data["withheld_bookings_count"] == 2
