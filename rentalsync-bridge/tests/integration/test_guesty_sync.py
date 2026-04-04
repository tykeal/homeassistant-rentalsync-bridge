# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""End-to-end integration test for Guesty PMS provider.

Tests the full flow: credential storage -> property sync -> reservation
sync with guest resolution -> iCal feed generation and content
verification, using mocked Guesty API responses and a real in-memory
SQLite database.
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
from src.models.oauth_credential import OAuthCredential
from src.providers.base import PMSGuest, PMSReservation
from src.repositories.credential_repository import CredentialRepository
from src.services.calendar_service import CalendarCache, CalendarService
from src.services.sync_service import SyncService

from tests.conftest import (
    make_listing,
    make_room,
)


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


class TestGuestyEndToEnd:
    """Full Guesty provider integration flow."""

    async def test_full_guesty_sync_flow(self, session, session_factory):
        """Test full Guesty sync flow."""
        # 1. Store Guesty credentials
        cred = OAuthCredential(
            pms_type="guesty",
            client_id="guesty_client_id",
            client_secret="guesty_secret",
        )
        session.add(cred)
        await session.commit()
        await session.refresh(cred)

        repo = CredentialRepository(session)
        stored = await repo.get_credential("guesty")
        assert stored is not None
        assert stored.client_id == "guesty_client_id"
        assert stored.pms_type == "guesty"

        # 2. Sync listings via mocked provider
        listing = make_listing(
            pms_id="guesty_prop_1",
            name="Guesty Beach Villa",
            slug="guesty-beach-villa",
        )
        session.add(listing)
        await session.commit()
        await session.refresh(listing)

        room = make_room(
            listing_id=listing.id,
            pms_room_id="guesty_room_1",
            room_name="Ocean Suite",
            slug="ocean-suite",
        )
        session.add(room)
        await session.commit()
        await session.refresh(room)

        # 3. Sync reservations with guest resolution
        now = datetime.now(UTC)
        mock_provider = AsyncMock()
        mock_provider.provider_type = "guesty"
        mock_provider.get_reservations = AsyncMock(
            return_value=[
                PMSReservation(
                    pms_booking_id="GR001",
                    listing_pms_id="guesty_prop_1",
                    guest_name=None,
                    guest_id="guest_42",
                    check_in=now + timedelta(days=5),
                    check_out=now + timedelta(days=8),
                    status="confirmed",
                    room_ids=("guesty_room_1",),
                    custom_data={},
                ),
            ]
        )
        mock_provider.get_guest = AsyncMock(
            return_value=PMSGuest(
                guest_id="guest_42",
                full_name="Alice Wonderland",
                phone="+15559876543",
                email="alice@example.com",
            )
        )

        cache = CalendarCache(ttl_seconds=0)
        sync = SyncService(
            session,
            calendar_cache=cache,
            session_factory=session_factory,
        )
        counts = await sync.sync_listing(listing, mock_provider)

        assert counts["inserted"] == 1
        assert counts["updated"] == 0

        # Verify guest name was resolved
        result = await session.execute(select(Booking))
        bookings = list(result.scalars().all())
        assert len(bookings) == 1
        assert bookings[0].guest_name == "Alice Wonderland"
        assert bookings[0].guest_phone_last4 == "6543"

        # 4. Generate iCal feed and verify content
        cal_service = CalendarService(cache=CalendarCache(ttl_seconds=0))
        ical_str = cal_service.generate_ical(listing, bookings)

        assert "BEGIN:VCALENDAR" in ical_str
        assert "BEGIN:VEVENT" in ical_str
        assert "Alice Wonderland" in ical_str

        cal = Calendar.from_ical(ical_str)
        events = list(cal.walk("VEVENT"))
        assert len(events) == 1

        evt = events[0]
        assert "uid" in evt
        assert "dtstart" in evt
        assert "dtend" in evt
        assert "summary" in evt
        assert "Alice Wonderland" in str(evt["summary"])

    async def test_guesty_multiple_reservations(self, session, session_factory):
        """Test syncing multiple reservations produces distinct iCal events."""
        listing = make_listing(
            pms_id="guesty_multi",
            name="Multi-Booking Villa",
            slug="guesty-multi-villa",
        )
        session.add(listing)
        await session.commit()
        await session.refresh(listing)

        room = make_room(
            listing_id=listing.id,
            pms_room_id="guesty_multi_room",
            room_name="Suite A",
            slug="suite-a",
        )
        session.add(room)
        await session.commit()
        await session.refresh(room)

        now = datetime.now(UTC)
        mock_provider = AsyncMock()
        mock_provider.provider_type = "guesty"
        mock_provider.get_reservations = AsyncMock(
            return_value=[
                PMSReservation(
                    pms_booking_id=f"GR{i:03d}",
                    listing_pms_id="guesty_multi",
                    guest_name=f"Guest {i}",
                    guest_id=None,
                    check_in=now + timedelta(days=i * 10),
                    check_out=now + timedelta(days=i * 10 + 3),
                    status="confirmed",
                    room_ids=("guesty_multi_room",),
                    custom_data={},
                )
                for i in range(1, 4)
            ]
        )
        mock_provider.get_guest = AsyncMock(return_value=None)

        cache = CalendarCache(ttl_seconds=0)
        sync = SyncService(
            session,
            calendar_cache=cache,
            session_factory=session_factory,
        )
        counts = await sync.sync_listing(listing, mock_provider)
        assert counts["inserted"] == 3

        result = await session.execute(
            select(Booking).where(Booking.listing_id == listing.id)
        )
        bookings = list(result.scalars().all())
        assert len(bookings) == 3

        cal_service = CalendarService(cache=CalendarCache(ttl_seconds=0))
        ical_str = cal_service.generate_ical(listing, bookings)
        cal = Calendar.from_ical(ical_str)
        events = list(cal.walk("VEVENT"))
        assert len(events) == 3

        uids = {str(e["uid"]) for e in events}
        assert len(uids) == 3
