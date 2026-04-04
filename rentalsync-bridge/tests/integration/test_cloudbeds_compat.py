# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Backward compatibility integration tests for Cloudbeds provider.

Verifies that pre-existing Cloudbeds installations continue to work
through the provider-agnostic pipeline, and that iCal feed URLs remain
stable after migration.
"""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from icalendar import Calendar
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from src.database import Base
from src.models.booking import Booking
from src.providers.base import PMSReservation
from src.services.calendar_service import CalendarCache, CalendarService
from src.services.sync_service import SyncService

from tests.conftest import make_booking, make_listing, make_room


@pytest.fixture
async def db_engine():
    """Create async in-memory SQLite engine."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def set_sqlite_pragma(dbapi_conn, _rec):
        """Enable SQLite FK constraints."""
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
def session_factory(db_engine):
    """Create async session factory bound to test engine."""
    return async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture
async def session(session_factory) -> AsyncGenerator[AsyncSession]:
    """Yield an async database session for each test."""
    async with session_factory() as s:
        yield s


class TestCloudbedsBackwardCompat:
    """Verify Cloudbeds continues to work through the new pipeline."""

    async def test_existing_cloudbeds_data_survives_sync(
        self, session, session_factory
    ):
        """Pre-existing data + re-sync produces correct iCal."""
        # Simulate pre-existing Cloudbeds data (no room association)
        listing = make_listing(
            pms_id="cb_prop_100",
            name="Cloudbeds Lodge",
            slug="cloudbeds-lodge",
        )
        session.add(listing)
        await session.commit()
        await session.refresh(listing)

        now = datetime.now(UTC)
        old_booking = make_booking(
            listing_id=listing.id,
            pms_booking_id="CB_BK001",
            guest_name="Bob Legacy",
            check_in_date=now + timedelta(days=14),
            check_out_date=now + timedelta(days=18),
        )
        session.add(old_booking)
        await session.commit()
        await session.refresh(old_booking)

        # Generate iCal BEFORE re-sync
        cal_service = CalendarService(cache=CalendarCache(ttl_seconds=0))
        ical_before = cal_service.generate_ical(listing, [old_booking])
        cal_before = Calendar.from_ical(ical_before)
        uid_before = str(next(iter(cal_before.walk("VEVENT")))["uid"])

        # Re-sync via provider-agnostic pipeline (Cloudbeds mock)
        # Use empty room_ids so booking ID stays "CB_BK001"
        mock_provider = AsyncMock()
        mock_provider.provider_type = "cloudbeds"
        mock_provider.get_reservations = AsyncMock(
            return_value=[
                PMSReservation(
                    pms_booking_id="CB_BK001",
                    listing_pms_id="cb_prop_100",
                    guest_name="Bob Legacy",
                    guest_id=None,
                    check_in=now + timedelta(days=14),
                    check_out=now + timedelta(days=18),
                    status="confirmed",
                    room_ids=(),
                    custom_data={},
                ),
            ]
        )
        mock_provider.get_guest = AsyncMock(return_value=None)

        sync = SyncService(
            session,
            calendar_cache=CalendarCache(ttl_seconds=0),
            session_factory=session_factory,
        )
        counts = await sync.sync_listing(listing, mock_provider)

        # Should update, not insert (same booking ID)
        assert counts["inserted"] == 0
        assert counts["updated"] == 1
        assert counts["cancelled"] == 0

        # iCal after sync should have same UID (stable)
        result = await session.execute(
            select(Booking).where(Booking.listing_id == listing.id)
        )
        bookings_after = list(result.scalars().all())
        assert len(bookings_after) == 1

        ical_after = cal_service.generate_ical(listing, bookings_after)
        cal_after = Calendar.from_ical(ical_after)
        uid_after = str(next(iter(cal_after.walk("VEVENT")))["uid"])
        assert uid_after == uid_before

    async def test_ical_url_slug_stable_after_migration(self, session, session_factory):
        """iCal URL slugs remain unchanged after migration."""
        original_slug = "my-cloudbeds-property"
        listing = make_listing(
            pms_id="cb_prop_200",
            name="CB Property",
            slug=original_slug,
        )
        session.add(listing)
        await session.commit()
        await session.refresh(listing)

        assert listing.ical_url_slug == original_slug

        room_slug = "standard-room"
        room = make_room(
            listing_id=listing.id,
            pms_room_id="cb_room_2",
            room_name="Standard Room",
            slug=room_slug,
        )
        session.add(room)
        await session.commit()
        await session.refresh(room)

        # Slug should remain unchanged
        assert listing.ical_url_slug == original_slug
        assert room.ical_url_slug == room_slug

    async def test_cloudbeds_sync_with_new_reservation(self, session, session_factory):
        """New reservations from Cloudbeds sync correctly."""
        listing = make_listing(
            pms_id="cb_prop_300",
            name="CB New Reservation Property",
            slug="cb-new-res",
        )
        session.add(listing)
        await session.commit()
        await session.refresh(listing)

        room = make_room(
            listing_id=listing.id,
            pms_room_id="cb_room_3",
            room_name="Deluxe Room",
            slug="deluxe-room",
        )
        session.add(room)
        await session.commit()
        await session.refresh(room)

        now = datetime.now(UTC)
        mock_provider = AsyncMock()
        mock_provider.provider_type = "cloudbeds"
        mock_provider.get_reservations = AsyncMock(
            return_value=[
                PMSReservation(
                    pms_booking_id="CB_NEW_001",
                    listing_pms_id="cb_prop_300",
                    guest_name="Charlie Cloudbeds",
                    guest_id=None,
                    check_in=now + timedelta(days=20),
                    check_out=now + timedelta(days=25),
                    status="confirmed",
                    room_ids=("cb_room_3",),
                    custom_data={},
                ),
            ]
        )
        mock_provider.get_guest = AsyncMock(return_value=None)

        sync = SyncService(
            session,
            calendar_cache=CalendarCache(ttl_seconds=0),
            session_factory=session_factory,
        )
        counts = await sync.sync_listing(listing, mock_provider)
        assert counts["inserted"] == 1

        result = await session.execute(
            select(Booking).where(Booking.listing_id == listing.id)
        )
        bookings = list(result.scalars().all())
        assert bookings[0].guest_name == "Charlie Cloudbeds"

        cal_service = CalendarService(cache=CalendarCache(ttl_seconds=0))
        ical_str = cal_service.generate_ical(listing, bookings)
        assert "Charlie Cloudbeds" in ical_str
        assert "BEGIN:VEVENT" in ical_str
