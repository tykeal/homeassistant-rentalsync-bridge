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


# ---------------------------------------------------------------------------
# Guesty Reservations V3 synthetic payloads (spec 004, T001)
# ---------------------------------------------------------------------------
#
# Built from contracts/guesty-v3-search.md, contracts/guesty-v3-by-ids.md and
# contracts/guesty-guests-crud.md.  Every value is synthetic: the
# ``check-fixture-pii`` hook scans this file.

# The documented 12-value V3 status enum (research R7).
GUESTY_V3_STATUSES: tuple[str, ...] = (
    "confirmed",
    "reserved",
    "awaiting_payment",
    "inquiry",
    "canceled",
    "closed",
    "declined",
    "expired",
    "alteration",
    "completed",
    "checked_in",
    "checked_out",
)

# Synthetic stay instants: 15:00 / 11:00 UTC, so a UTC-timezone listing's
# localized dates equal the UTC dates.
_V3_CHECK_IN_HOUR = 15
_V3_CHECK_OUT_HOUR = 11


def _v3_iso(value: datetime) -> str:
    """Render an aware datetime the way Guesty V3 does (UTC, ``Z``)."""
    return (
        value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    )


class GuestyV3Payloads:
    """Factory for synthetic Guesty Reservations V3 payloads.

    IDs are 24-character lowercase hex strings (Mongo ObjectId shaped) that
    increase with creation order, so items built later sort after earlier
    ones under ``sort=_id``.
    """

    def __init__(self, now: datetime | None = None) -> None:
        """Anchor relative dates to *now* (default: the current UTC day)."""
        reference = now or datetime.now(UTC)
        self.today = reference.astimezone(UTC).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        self._counter = 0

    # -- identifiers and dates ----------------------------------------------

    def new_id(self, kind: str = "res") -> str:
        """Return a fresh, ascending, ObjectId-shaped identifier."""
        self._counter += 1
        prefix = {"res": "6650", "lst": "6640", "gst": "6630", "cf": "6620"}
        return f"{prefix.get(kind, '6600')}{self._counter:020x}"

    def day(self, offset: int, hour: int = 0) -> datetime:
        """Return the UTC instant *offset* days from today at *hour*."""
        return self.today + timedelta(days=offset, hours=hour)

    def check_in_at(self, offset: int) -> datetime:
        """Return a synthetic check-in instant *offset* days from today."""
        return self.day(offset, _V3_CHECK_IN_HOUR)

    def check_out_at(self, offset: int) -> datetime:
        """Return a synthetic check-out instant *offset* days from today."""
        return self.day(offset, _V3_CHECK_OUT_HOUR)

    # -- search shape --------------------------------------------------------

    @staticmethod
    def stay_segment(
        listing_id: str,
        check_in: datetime,
        check_out: datetime,
        *,
        parent_listing_id: str | None = None,
        guests_count: int | None = 2,
        timezone: str = "UTC",
    ) -> dict[str, Any]:
        """Build one search-shape ``stay[]`` segment.

        Args:
            listing_id: Segment ``listingId`` (``unitId ?? unitTypeId``).
            check_in: Segment start instant.
            check_out: Segment end instant.
            parent_listing_id: Multi-unit parent; omitted when None.
            guests_count: Segment ``guestsCount``; omitted when None.
            timezone: Listing timezone used for the localized dates.
        """
        from zoneinfo import ZoneInfo

        tz = ZoneInfo(timezone)
        segment: dict[str, Any] = {
            "listingId": listing_id,
            "checkIn": _v3_iso(check_in),
            "checkOut": _v3_iso(check_out),
            "checkInDateLocalized": check_in.astimezone(tz).date().isoformat(),
            "checkOutDateLocalized": check_out.astimezone(tz).date().isoformat(),
        }
        if parent_listing_id is not None:
            segment["parentListingId"] = parent_listing_id
        if guests_count is not None:
            segment["guestsCount"] = guests_count
        return segment

    def search_item(
        self,
        listing_id: str | None = None,
        *,
        reservation_id: str | None = None,
        status: str | None = "confirmed",
        check_in: int | datetime = 7,
        check_out: int | datetime = 10,
        parent_listing_id: str | None = None,
        stay: list[dict[str, Any]] | None = None,
        guest_id: str | None = None,
        confirmation_code: str | None = None,
        source: str = "direct",
        notes: dict[str, str] | None = None,
        custom_fields: Any = None,
        guests_count: int | None = 2,
        timezone: str = "UTC",
    ) -> dict[str, Any]:
        """Build one search-shape reservation item.

        Args:
            listing_id: Listing of the single segment (ignored with *stay*).
            reservation_id: ``reservationId``; generated when None.
            status: V3 status; ``None`` omits the key entirely.
            check_in: Day offset or instant for a single-segment stay.
            check_out: Day offset or instant for a single-segment stay.
            parent_listing_id: Multi-unit parent of the single segment.
            stay: Explicit ``stay[]`` segments (multi-segment shapes).
            guest_id: ``guestId``; generated when None.
            confirmation_code: ``confirmationCode``; generated when None.
            source: Booking channel.
            notes: ``notes`` object; a synthetic default when None.
            custom_fields: ``customFields`` value; omitted when None.
            guests_count: Single-segment ``guestsCount``.
            timezone: Listing timezone for the localized dates.
        """
        rid = reservation_id or self.new_id("res")
        if stay is None:
            if listing_id is None:
                msg = "search_item needs listing_id or stay"
                raise ValueError(msg)
            start = (
                check_in
                if isinstance(check_in, datetime)
                else self.check_in_at(check_in)
            )
            end = (
                check_out
                if isinstance(check_out, datetime)
                else self.check_out_at(check_out)
            )
            stay = [
                self.stay_segment(
                    listing_id,
                    start,
                    end,
                    parent_listing_id=parent_listing_id,
                    guests_count=guests_count,
                    timezone=timezone,
                )
            ]
        item: dict[str, Any] = {
            "reservationId": rid,
            "guestId": guest_id or self.new_id("gst"),
            "confirmationCode": confirmation_code or f"SYN-{rid[-6:].upper()}",
            "source": source,
            "checkIn": stay[0]["checkIn"],
            "checkOut": stay[-1]["checkOut"],
            "checkInDateLocalized": stay[0]["checkInDateLocalized"],
            "checkOutDateLocalized": stay[-1]["checkOutDateLocalized"],
            "notes": notes
            if notes is not None
            else {
                "other": "Synthetic other note",
                "guest": "Synthetic guest note",
                "specialRequests": "",
                "cleaning": "Synthetic cleaning note",
                "keyCode": "0000",
            },
            "stay": stay,
        }
        if status is not None:
            item["status"] = status
        if custom_fields is not None:
            item["customFields"] = custom_fields
        return item

    def custom_fields(self, values: dict[str, Any]) -> list[dict[str, Any]]:
        """Build an inline ``customFields`` list from ``{fieldId: value}``."""
        return [
            {"_id": self.new_id("cf"), "fieldId": field_id, "value": value}
            for field_id, value in values.items()
        ]

    @staticmethod
    def search_page(
        results: list[dict[str, Any]],
        skip: int = 0,
        limit: int = 100,
        has_more: bool = False,
    ) -> dict[str, Any]:
        """Wrap *results* in a search response page."""
        return {
            "results": results,
            "pagination": {"skip": skip, "limit": limit, "hasMore": has_more},
        }

    def items_for_every_status(
        self, listing_id: str, **kwargs: Any
    ) -> list[dict[str, Any]]:
        """Build one single-segment search item per documented V3 status."""
        return [
            self.search_item(listing_id, status=status, **kwargs)
            for status in GUESTY_V3_STATUSES
        ]

    # -- multi-segment shapes (research R6) ----------------------------------

    def _segments(
        self,
        rooms: list[tuple[str, str | None]],
        start: int,
        nights: list[int],
        gaps: list[int] | None = None,
    ) -> list[dict[str, Any]]:
        """Build consecutive segments; a gap of 0 days makes them adjacent.

        Adjacent segments share one instant (``prev.checkOut ==
        next.checkIn``); a positive gap moves the next check-in to the
        check-in hour *gap* days after the previous check-out day.
        """
        gaps = gaps or [0] * (len(rooms) - 1)
        segments: list[dict[str, Any]] = []
        cursor = self.check_in_at(start)
        for index, ((listing_id, parent), length) in enumerate(
            zip(rooms, nights, strict=True)
        ):
            end = cursor + timedelta(days=length)
            segments.append(
                self.stay_segment(listing_id, cursor, end, parent_listing_id=parent)
            )
            if index < len(gaps):
                cursor = end + timedelta(days=gaps[index]) if gaps[index] else end
        return segments

    def same_property_move(
        self, parent_id: str, unit_a: str, unit_b: str, *, start: int = 5, **kwargs: Any
    ) -> dict[str, Any]:
        """A multi-unit stay moved between two sub-units of one property."""
        stay = self._segments([(unit_a, parent_id), (unit_b, parent_id)], start, [2, 2])
        return self.search_item(stay=stay, **kwargs)

    def tracked_listing_move(
        self, listing_a: str, listing_b: str, *, start: int = 5, **kwargs: Any
    ) -> dict[str, Any]:
        """A stay starting in tracked listing A and moving to listing B."""
        stay = self._segments([(listing_a, None), (listing_b, None)], start, [2, 2])
        return self.search_item(stay=stay, **kwargs)

    def round_trip_move(
        self, parent_id: str, unit_a: str, unit_b: str, *, start: int = 5, **kwargs: Any
    ) -> dict[str, Any]:
        """A → B → A, all adjacent: intervals A, B and A#2."""
        stay = self._segments(
            [(unit_a, parent_id), (unit_b, parent_id), (unit_a, parent_id)],
            start,
            [2, 2, 2],
        )
        return self.search_item(stay=stay, **kwargs)

    def unassigned_parent(
        self, parent_id: str, *, check_in: int = 7, check_out: int = 10, **kwargs: Any
    ) -> dict[str, Any]:
        """A multi-unit stay not yet assigned to a sub-unit (FR-019)."""
        return self.search_item(
            parent_id, check_in=check_in, check_out=check_out, **kwargs
        )

    def rearranged_into_untracked(
        self, untracked_id: str, tracked_id: str, *, start: int = 5, **kwargs: Any
    ) -> dict[str, Any]:
        """A stored stay re-arranged to start in an untracked property.

        The first segment is untracked, so a listing-filtered search no
        longer returns it; the later tracked segment keeps its room
        (FR-011a re-attribution).  Pass the stored ``reservation_id``.
        """
        stay = self._segments([(untracked_id, None), (tracked_id, None)], start, [2, 3])
        return self.search_item(stay=stay, **kwargs)

    def never_stored_untracked_start(
        self, untracked_id: str, tracked_id: str, *, start: int = 5, **kwargs: Any
    ) -> dict[str, Any]:
        """A never-stored stay starting in an untracked property (FR-016 gap)."""
        stay = self._segments([(untracked_id, None), (tracked_id, None)], start, [3, 2])
        return self.search_item(stay=stay, **kwargs)

    # -- by-IDs shape --------------------------------------------------------

    def by_ids_item(
        self,
        reservation_id: str,
        stay: list[dict[str, Any]],
        *,
        status: str = "confirmed",
        guest_id: str | None = None,
    ) -> dict[str, Any]:
        """Build one by-IDs item (``_id``, ``unitId``/``unitTypeId``)."""
        item: dict[str, Any] = {"_id": reservation_id, "status": status, "stay": stay}
        if guest_id is not None:
            item["guestId"] = guest_id
        return item

    @staticmethod
    def by_ids_segment(
        unit_type_id: str,
        check_in_local: str,
        check_out_local: str,
        *,
        unit_id: str | None = None,
    ) -> dict[str, Any]:
        """Build one by-IDs ``stay[]`` segment (localized dates only)."""
        segment: dict[str, Any] = {
            "unitTypeId": unit_type_id,
            "checkInDateLocalized": check_in_local,
            "checkOutDateLocalized": check_out_local,
        }
        if unit_id is not None:
            segment["unitId"] = unit_id
        return segment

    def as_by_ids(
        self,
        search_item: dict[str, Any],
        *,
        unassigned: frozenset[str] | set[str] = frozenset(),
    ) -> dict[str, Any]:
        """Describe the same reservation in the by-IDs shape.

        A sub-unit segment becomes ``unitTypeId`` = parent, ``unitId`` =
        unit; a single-unit segment carries the listing in both.  Segments
        whose ``listingId`` is in *unassigned* (multi-unit parents with no
        sub-unit yet) carry no ``unitId``.
        """
        stay: list[dict[str, Any]] = []
        for segment in search_item["stay"]:
            listing_id = segment["listingId"]
            parent = segment.get("parentListingId")
            if parent and parent != listing_id:
                unit_type_id, unit_id = parent, listing_id
            elif listing_id in unassigned:
                unit_type_id, unit_id = listing_id, None
            else:
                unit_type_id, unit_id = listing_id, listing_id
            stay.append(
                self.by_ids_segment(
                    unit_type_id,
                    segment["checkInDateLocalized"],
                    segment["checkOutDateLocalized"],
                    unit_id=unit_id,
                )
            )
        return self.by_ids_item(
            search_item["reservationId"],
            stay,
            status=search_item.get("status", "confirmed"),
            guest_id=search_item.get("guestId"),
        )

    # -- guests-crud ---------------------------------------------------------

    @staticmethod
    def guests_crud_body(
        guest_id: str,
        *,
        full_name: str = "Synthetic Guest",
        phone: str | None = "+15555550100",
        email: str | None = "guest@example.com",
    ) -> dict[str, Any]:
        """Build a ``GET /v1/guests-crud/{id}`` response body."""
        return {"_id": guest_id, "fullName": full_name, "phone": phone, "email": email}


@pytest.fixture
def guesty_v3_payloads() -> GuestyV3Payloads:
    """Synthetic Guesty Reservations V3 payload builders (spec 004)."""
    return GuestyV3Payloads()
