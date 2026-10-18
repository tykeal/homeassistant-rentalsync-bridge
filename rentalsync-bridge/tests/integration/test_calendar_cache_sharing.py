# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Regression tests: sync must invalidate the cache the iCal route serves.

Found while investigating issue #111.  ``src/api/ical.py`` used to build its
own module-level ``CalendarCache`` and serve every feed from it, while
``src/main.py`` / ``src/api/listings.py`` handed ``SyncService`` the *other*
global cache from ``src/services/calendar_service.py``.  Sync therefore
invalidated a cache nobody read and the served feed stayed stale until the
300 s TTL lapsed.
"""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from src.api import custom_fields as custom_fields_module
from src.api import ical as ical_module
from src.database import Base, get_db
from src.main import create_app
from src.models.booking import Booking
from src.models.listing import Listing
from src.models.room import Room
from src.services.calendar_service import get_calendar_cache
from src.services.sync_service import SyncService


def test_ical_route_and_sync_share_one_cache():
    """The cache sync invalidates must be the cache the route serves from."""
    shared = get_calendar_cache()

    assert ical_module.get_calendar_cache() is shared
    assert custom_fields_module.get_calendar_cache() is shared
    # The route's CalendarService must also be wired to that same instance.
    assert ical_module.get_calendar_service()._cache is shared


@pytest.fixture
async def cache_engine():
    """Create a test database engine with FK support.

    Yields:
        Async SQLAlchemy engine backed by an in-memory database.
    """
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
async def cache_app(cache_engine) -> AsyncGenerator:
    """Create a test app bound to the test engine.

    Yields:
        Configured FastAPI application.
    """
    app = create_app()
    session_factory = async_sessionmaker(
        cache_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async def override_get_db() -> AsyncGenerator[AsyncSession]:
        """Override the database session dependency.

        Yields:
            Test database session.
        """
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    yield app
    app.dependency_overrides.clear()


async def test_sync_invalidates_the_feed_the_endpoint_serves(
    cache_engine, cache_app
) -> None:
    """A sync must make the next iCal request reflect the new booking."""
    get_calendar_cache().clear()

    session_factory = async_sessionmaker(
        cache_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async with session_factory() as session:
        listing = Listing(
            pms_id="prop-cache",
            name="Cache Property",
            ical_url_slug="cache-property",
            timezone="UTC",
            enabled=True,
        )
        session.add(listing)
        await session.commit()
        await session.refresh(listing)

        room = Room(
            listing_id=listing.id,
            pms_room_id="room-cache",
            room_name="Main",
            ical_url_slug="main",
            enabled=True,
        )
        session.add(room)
        await session.commit()
        await session.refresh(room)

    transport = ASGITransport(app=cache_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.get("/ical/cache-property/main.ics")
        assert first.status_code == 200
        assert "CACHED-BOOKING" not in first.text

        # Warm the cache a second time to prove the route really caches.
        assert (await client.get("/ical/cache-property/main.ics")).text == first.text

        async with session_factory() as session:
            now = datetime.now(UTC)
            session.add(
                Booking(
                    listing_id=listing.id,
                    room_id=room.id,
                    pms_booking_id="CACHED-BOOKING",
                    guest_name="CACHED-BOOKING Guest",
                    check_in_date=now - timedelta(days=1),
                    check_out_date=now + timedelta(days=2),
                    status="confirmed",
                )
            )
            await session.commit()

            # Sync uses the global cache; the route must observe the eviction.
            sync = SyncService(session, calendar_cache=get_calendar_cache())
            sync._calendar_cache.invalidate_prefix(listing.ical_url_slug)

        second = await client.get("/ical/cache-property/main.ics")
        assert second.status_code == 200
        assert "CACHED-BOOKING" in second.text
