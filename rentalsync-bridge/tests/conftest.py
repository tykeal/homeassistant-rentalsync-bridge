# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Pytest fixtures for RentalSync Bridge tests."""

import os
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


# Set environment variables BEFORE any src imports
# This must happen at module load time
def _setup_env() -> None:
    """Set up test environment variables at module load."""
    if "ENCRYPTION_KEY" not in os.environ:
        test_key = Fernet.generate_key().decode()
        os.environ["ENCRYPTION_KEY"] = test_key
    if "DATABASE_URL" not in os.environ:
        os.environ["DATABASE_URL"] = "sqlite:///./test.db"
    if "STANDALONE_MODE" not in os.environ:
        os.environ["STANDALONE_MODE"] = "true"
    if "CLOUDBEDS_CLIENT_ID" not in os.environ:
        os.environ["CLOUDBEDS_CLIENT_ID"] = "test_client_id"
    if "CLOUDBEDS_CLIENT_SECRET" not in os.environ:
        os.environ["CLOUDBEDS_CLIENT_SECRET"] = "test_client_secret"
    if "LOG_LEVEL" not in os.environ:
        os.environ["LOG_LEVEL"] = "DEBUG"


_setup_env()

# Now safe to import from src
from fastapi import FastAPI  # noqa: E402
from src.database import Base  # noqa: E402
from src.models.booking import Booking  # noqa: E402
from src.models.listing import Listing  # noqa: E402
from src.models.room import Room  # noqa: E402
from src.providers.base import (  # noqa: E402
    PMSGuest,
    PMSListing,
    PMSProvider,
    PMSReservation,
    PMSRoom,
)


@pytest.fixture(scope="session", autouse=True)
def setup_test_environment():
    """Set up test environment variables."""
    # Already set by _setup_env(), just yield and cleanup
    yield
    # Cleanup
    test_db_path = Path("test.db")
    if test_db_path.exists():
        test_db_path.unlink()


@pytest.fixture
async def async_engine():
    """Create an async test database engine."""
    from sqlalchemy import event

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
        connect_args={"check_same_thread": False},
    )

    # Enable foreign key constraint enforcement for SQLite on every connection
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
async def async_session(async_engine) -> AsyncGenerator[AsyncSession]:
    """Create an async test database session."""
    session_factory = async_sessionmaker(
        async_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )
    async with session_factory() as session:
        yield session
        await session.rollback()


@pytest.fixture
def async_session_factory(async_engine):
    """Create an async session factory for tests needing session_factory."""
    return async_sessionmaker(
        async_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )


@pytest.fixture
def app() -> FastAPI:
    """Create a test FastAPI application."""
    from src.main import create_app

    return create_app()


@pytest.fixture
async def client(app: FastAPI) -> AsyncGenerator[AsyncClient]:
    """Create an async test client."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
def sample_listing_data() -> dict[str, Any]:
    """Sample listing data for tests."""
    return {
        "pms_id": "test_property_123",
        "name": "Test Property",
        "enabled": True,
        "ical_url_slug": "test-property-123",
        "timezone": "America/New_York",
        "sync_enabled": True,
    }


@pytest.fixture
def sample_booking_data() -> dict[str, Any]:
    """Sample booking data for tests."""
    return {
        "pms_booking_id": "booking_456",
        "guest_name": "John Smith",
        "check_in_date": "2026-02-01",
        "check_out_date": "2026-02-05",
        "status": "confirmed",
        "phone_last4": "1234",
        "source": "direct",
    }


@pytest.fixture
def encryption_key(setup_test_environment) -> str:
    """Get test encryption key."""
    return os.environ["ENCRYPTION_KEY"]


# ---------------------------------------------------------------------------
# Multi-provider factory helpers (Phase 7 - T052)
# ---------------------------------------------------------------------------


def make_listing(
    *,
    pms_id: str = "PROP001",
    name: str = "Test Property",
    slug: str = "test-property",
    timezone: str = "America/New_York",
    enabled: bool = True,
    sync_enabled: bool = True,
) -> Listing:
    """Create a Listing model instance for tests."""
    return Listing(
        pms_id=pms_id,
        name=name,
        ical_url_slug=slug,
        timezone=timezone,
        enabled=enabled,
        sync_enabled=sync_enabled,
    )


def make_room(
    *,
    listing_id: int = 1,
    pms_room_id: str = "ROOM001",
    room_name: str = "Main Room",
    slug: str = "main-room",
    room_type_name: str | None = None,
    enabled: bool = True,
) -> Room:
    """Create a Room model instance for tests."""
    return Room(
        listing_id=listing_id,
        pms_room_id=pms_room_id,
        room_name=room_name,
        ical_url_slug=slug,
        room_type_name=room_type_name,
        enabled=enabled,
    )


def make_booking(
    *,
    listing_id: int = 1,
    room_id: int | None = None,
    pms_booking_id: str = "BK001",
    guest_name: str = "Jane Doe",
    guest_phone_last4: str | None = None,
    check_in_date: datetime | None = None,
    check_out_date: datetime | None = None,
    status: str = "confirmed",
    custom_data: dict[str, Any] | None = None,
) -> Booking:
    """Create a Booking model instance for tests."""
    now = datetime.now(UTC)
    return Booking(
        listing_id=listing_id,
        room_id=room_id,
        pms_booking_id=pms_booking_id,
        guest_name=guest_name,
        guest_phone_last4=guest_phone_last4,
        check_in_date=check_in_date or now + timedelta(days=7),
        check_out_date=check_out_date or now + timedelta(days=10),
        status=status,
        custom_data=custom_data,
    )


def make_pms_listing(
    *,
    pms_id: str = "PROP001",
    name: str = "Test Property",
    timezone: str = "America/New_York",
    address: str | None = None,
    rooms: tuple[PMSRoom, ...] = (),
) -> PMSListing:
    """Create a PMSListing DTO for tests."""
    return PMSListing(
        pms_id=pms_id,
        name=name,
        timezone=timezone,
        address=address,
        rooms=rooms,
    )


def make_pms_room(
    *,
    pms_room_id: str = "ROOM001",
    name: str = "Main Room",
    room_type: str | None = None,
) -> PMSRoom:
    """Create a PMSRoom DTO for tests."""
    return PMSRoom(
        pms_room_id=pms_room_id,
        name=name,
        room_type=room_type,
    )


def make_pms_reservation(
    *,
    pms_booking_id: str = "RES001",
    listing_pms_id: str = "PROP001",
    guest_name: str | None = "Jane Doe",
    guest_id: str | None = None,
    check_in: datetime | None = None,
    check_out: datetime | None = None,
    status: str = "confirmed",
    room_ids: tuple[str, ...] = ("ROOM001",),
    custom_data: dict[str, Any] | None = None,
) -> PMSReservation:
    """Create a PMSReservation DTO for tests."""
    now = datetime.now(UTC)
    return PMSReservation(
        pms_booking_id=pms_booking_id,
        listing_pms_id=listing_pms_id,
        guest_name=guest_name,
        guest_id=guest_id,
        check_in=check_in or now + timedelta(days=7),
        check_out=check_out or now + timedelta(days=10),
        status=status,
        room_ids=room_ids,
        custom_data=custom_data or {},
    )


def make_pms_guest(
    *,
    guest_id: str = "GUEST001",
    full_name: str = "Jane Doe",
    phone: str | None = "+15551234567",
    email: str | None = "jane@example.com",
) -> PMSGuest:
    """Create a PMSGuest DTO for tests."""
    return PMSGuest(
        guest_id=guest_id,
        full_name=full_name,
        phone=phone,
        email=email,
    )


def make_mock_provider(
    *,
    provider_type: str = "guesty",
    listings: list[PMSListing] | None = None,
    reservations: list[PMSReservation] | None = None,
    rooms: list[PMSRoom] | None = None,
    guest: PMSGuest | None = None,
) -> AsyncMock:
    """Create a mock PMSProvider with configurable return values."""
    mock = AsyncMock(spec=PMSProvider)
    mock.provider_type = provider_type
    mock.get_listings = AsyncMock(return_value=listings or [])
    mock.get_reservations = AsyncMock(return_value=reservations or [])
    mock.get_rooms = AsyncMock(return_value=rooms or [])
    mock.get_guest = AsyncMock(return_value=guest)
    mock.get_custom_fields = AsyncMock(return_value={})
    return mock
