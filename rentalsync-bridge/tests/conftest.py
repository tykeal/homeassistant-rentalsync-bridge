# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Pytest fixtures for RentalSync Bridge tests."""

import json
import os
import re
from collections import Counter
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import httpx
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


# ---------------------------------------------------------------------------
# Counting fake Guesty transport (spec 004, T002)
# ---------------------------------------------------------------------------

# Resource names used for ``GuestyFakeTransport.counts`` and fault keys.
GUESTY_RESOURCES: tuple[str, ...] = (
    "listings",
    "listing",
    "reservations",
    "v3_search",
    "v3_by_ids",
    "custom_fields",
    "guests",
    "guests_crud",
    "account",
    "custom_field_definitions",
)

# Search-page faults applied to the Nth page (0-based) of any search.
SEARCH_PAGE_FAULTS: tuple[str, ...] = (
    "empty_has_more",
    "short_has_more",
    "duplicate",
    "out_of_order",
)


@dataclass(frozen=True)
class RecordedRequest:
    """One request seen by ``GuestyFakeTransport``."""

    method: str
    path: str
    resource: str
    query: dict[str, list[str]]


@dataclass
class _StatusFault:
    """A queued HTTP error for one resource."""

    status: int
    remaining: int
    body: Any
    retry_after: str | None


def _first_listing_refs(item: dict[str, Any]) -> set[str]:
    """Return the first segment's ``listingId``/``parentListingId``."""
    stay = item.get("stay") or [{}]
    first = stay[0]
    return {str(first[k]) for k in ("listingId", "parentListingId") if first.get(k)}


class GuestyFakeTransport:
    """A counting, filtering fake of the Guesty Open API.

    Holds one account's data and serves it through ``httpx.MockTransport``,
    applying the server-side filters Guesty documents so that narrowing and
    the FR-016 residual gap are observable in tests.  Every request is
    recorded (``requests``) and counted per resource (``counts``).

    Attributes:
        listings: Listing documents keyed by ``_id``.
        legacy_reservations: ``GET /v1/reservations`` items.
        v3_items: Search-shape V3 items (the by-IDs view is derived).
        by_ids_overrides: Explicit by-IDs items keyed by ``_id``.
        custom_fields: Explicit custom-field lists keyed by reservation.
        guests: Guest bodies keyed by guest ID (both guest endpoints).
        ignore_reservation_ids: By-IDs ignores ``reservationIds`` and
            returns up to ``ignored_ids_limit`` account items instead.
    """

    def __init__(self, payloads: GuestyV3Payloads | None = None) -> None:
        """Create an empty account."""
        self.payloads = payloads or GuestyV3Payloads()
        self.listings: dict[str, dict[str, Any]] = {}
        self.legacy_reservations: list[dict[str, Any]] = []
        self.v3_items: list[dict[str, Any]] = []
        self.by_ids_overrides: dict[str, dict[str, Any]] = {}
        self.custom_fields: dict[str, list[dict[str, Any]]] = {}
        self.guests: dict[str, dict[str, Any]] = {}
        self.ignore_reservation_ids = False
        self.ignored_ids_limit = 10
        self.requests: list[RecordedRequest] = []
        self.counts: Counter[str] = Counter()
        self._status_faults: dict[str, list[_StatusFault]] = {}
        self._page_faults: dict[int, str] = {}

    # -- setup ---------------------------------------------------------------

    def add_listing(
        self,
        listing_id: str,
        *,
        title: str = "Synthetic Listing",
        children: list[str] | None = None,
        parent_id: str | None = None,
        timezone: str = "UTC",
    ) -> dict[str, Any]:
        """Register a listing document (and its child sub-units)."""
        document: dict[str, Any] = {
            "_id": listing_id,
            "title": title,
            "timezone": timezone,
        }
        if children:
            document["childListings"] = [
                {"_id": child, "title": f"{title} unit {n}"}
                for n, child in enumerate(children, start=1)
            ]
            for child in children:
                self.listings.setdefault(
                    child,
                    {
                        "_id": child,
                        "title": f"{title} child",
                        "timezone": timezone,
                        "mtl": {"p": listing_id},
                    },
                )
        if parent_id:
            document["mtl"] = {"p": parent_id}
        self.listings[listing_id] = document
        return document

    def fail(
        self,
        resource: str,
        status: int,
        *,
        times: int = 1,
        body: Any = None,
        retry_after: str | None = "0",
    ) -> None:
        """Queue *times* HTTP *status* responses for *resource*."""
        if resource not in GUESTY_RESOURCES:
            msg = f"unknown Guesty resource {resource!r}"
            raise ValueError(msg)
        self._status_faults.setdefault(resource, []).append(
            _StatusFault(status, times, body, retry_after)
        )

    def fault_search_page(self, page: int, kind: str) -> None:
        """Corrupt the *page*-th (0-based) page of every search sequence."""
        if kind not in SEARCH_PAGE_FAULTS:
            msg = f"unknown search fault {kind!r}"
            raise ValueError(msg)
        self._page_faults[page] = kind

    def requests_for(self, resource: str) -> list[RecordedRequest]:
        """Return the recorded requests for one resource."""
        return [r for r in self.requests if r.resource == resource]

    # -- httpx wiring --------------------------------------------------------

    def client(self) -> httpx.AsyncClient:
        """Return an ``httpx.AsyncClient`` routed to this fake."""
        from src.providers.guesty.service import GUESTY_BASE_URL

        return httpx.AsyncClient(
            transport=httpx.MockTransport(self.handle),
            base_url=GUESTY_BASE_URL,
        )

    def provider(self, **kwargs: Any) -> Any:
        """Return a ``GuestyProvider`` wired to this fake."""
        from src.providers.guesty.service import GuestyProvider

        token_manager = AsyncMock()
        token_manager.get_token = AsyncMock(return_value="synthetic-token")
        return GuestyProvider(
            token_manager=token_manager, http_client=self.client(), **kwargs
        )

    @staticmethod
    def _resource(path: str) -> str:
        """Classify a request path into a resource name."""
        routes: tuple[tuple[str, str], ...] = (
            (r"/v1/listings", "listings"),
            (r"/v1/listings/[^/]+", "listing"),
            (r"/v1/reservations", "reservations"),
            (r"/v1/reservations-v3/search", "v3_search"),
            (r"/v1/reservations-v3", "v3_by_ids"),
            (r"/v1/reservations-v3/[^/]+/custom-fields", "custom_fields"),
            (r"/v1/guests/[^/]+", "guests"),
            (r"/v1/guests-crud/[^/]+", "guests_crud"),
            (r"/v1/accounts/me", "account"),
            (r"/v1/accounts/[^/]+/custom-fields", "custom_field_definitions"),
        )
        for pattern, name in routes:
            if re.fullmatch(pattern, path):
                return name
        return "unknown"

    def handle(self, request: httpx.Request) -> httpx.Response:
        """Serve one request (the ``httpx.MockTransport`` handler)."""
        path = request.url.path
        resource = self._resource(path)
        query = {key: request.url.params.get_list(key) for key in request.url.params}
        self.requests.append(RecordedRequest(request.method, path, resource, query))
        self.counts[resource] += 1

        faults = self._status_faults.get(resource)
        if faults:
            fault = faults[0]
            fault.remaining -= 1
            if fault.remaining <= 0:
                faults.pop(0)
            headers = {"Retry-After": fault.retry_after} if fault.retry_after else {}
            return httpx.Response(
                fault.status,
                json=fault.body if fault.body is not None else {"error": "fault"},
                headers=headers,
            )

        handler = getattr(self, f"_serve_{resource}", None)
        if handler is None:
            return httpx.Response(404, json={"error": "not found"})
        response: httpx.Response = handler(path, request.url.params)
        return response

    # -- resources -----------------------------------------------------------

    @staticmethod
    def _int(params: httpx.QueryParams, key: str, default: int) -> int:
        """Read an integer query parameter."""
        value = params.get(key)
        return int(value) if value is not None else default

    def _serve_listings(self, _path: str, params: httpx.QueryParams) -> httpx.Response:
        """Serve ``GET /v1/listings`` (skip/limit paged)."""
        skip = self._int(params, "skip", 0)
        limit = self._int(params, "limit", 100)
        docs = list(self.listings.values())
        return httpx.Response(
            200,
            json={
                "results": docs[skip : skip + limit],
                "count": len(docs),
                "skip": skip,
                "limit": limit,
            },
        )

    def _serve_listing(self, path: str, _params: httpx.QueryParams) -> httpx.Response:
        """Serve ``GET /v1/listings/{id}``."""
        document = self.listings.get(path.rsplit("/", 1)[-1])
        if document is None:
            return httpx.Response(404, json={"error": "listing not found"})
        return httpx.Response(200, json=document)

    def _serve_reservations(
        self, _path: str, params: httpx.QueryParams
    ) -> httpx.Response:
        """Serve legacy ``GET /v1/reservations`` with its ``$in`` filter."""
        wanted: set[str] | None = None
        raw_filters = params.get("filters")
        if raw_filters:
            for clause in json.loads(raw_filters):
                if clause.get("field") == "listingId" and clause.get("operator") == (
                    "$in"
                ):
                    wanted = {str(v) for v in clause.get("value", [])}
        items = [
            item
            for item in self.legacy_reservations
            if wanted is None or str(item.get("listingId")) in wanted
        ]
        skip = self._int(params, "skip", 0)
        limit = self._int(params, "limit", 100)
        return httpx.Response(
            200,
            json={
                "results": items[skip : skip + limit],
                "count": len(items),
                "skip": skip,
                "limit": limit,
            },
        )

    def search_matches(self, item: dict[str, Any], params: httpx.QueryParams) -> bool:
        """Apply the documented server-side search filters to one item."""
        raw_ids = params.get("filter[listingId][in]")
        if raw_ids is not None:
            wanted = {part for part in raw_ids.split(",") if part}
            if not _first_listing_refs(item) & wanted:
                return False
        gte = params.get("filter[checkOut][gte]")
        if gte is not None and str(item.get("checkOut", ""))[:10] < gte:
            return False
        lte = params.get("filter[checkIn][lte]")
        return lte is None or str(item.get("checkIn", ""))[:10] <= lte

    def _serve_v3_search(self, _path: str, params: httpx.QueryParams) -> httpx.Response:
        """Serve ``GET /v1/reservations-v3/search`` with filters and paging."""
        items = [item for item in self.v3_items if self.search_matches(item, params)]
        if params.get("sort") == "_id":
            items.sort(key=lambda item: str(item["reservationId"]))
        skip = self._int(params, "skip", 0)
        limit = self._int(params, "limit", 100)
        page = items[skip : skip + limit]
        has_more = skip + limit < len(items)
        fault = self._page_faults.get(skip // limit if limit else 0)
        if fault == "empty_has_more":
            page, has_more = [], True
        elif fault == "short_has_more" and page:
            page, has_more = page[:-1], True
        elif fault == "duplicate" and page:
            page = [*page, page[-1]]
        elif fault == "out_of_order" and len(page) > 1:
            page = [page[1], page[0], *page[2:]]
        return httpx.Response(
            200, json=self.payloads.search_page(page, skip, limit, has_more)
        )

    def by_ids_view(self) -> dict[str, dict[str, Any]]:
        """Return every account reservation in the by-IDs shape."""
        view = {
            str(item["reservationId"]): self.payloads.as_by_ids(item)
            for item in self.v3_items
        }
        view.update(self.by_ids_overrides)
        return view

    def _serve_v3_by_ids(self, _path: str, params: httpx.QueryParams) -> httpx.Response:
        """Serve ``GET /v1/reservations-v3?reservationIds=…`` (a list)."""
        view = self.by_ids_view()
        if self.ignore_reservation_ids:
            ordered = [view[key] for key in sorted(view)]
            return httpx.Response(200, json=ordered[: self.ignored_ids_limit])
        requested = params.get_list("reservationIds")
        return httpx.Response(200, json=[view[rid] for rid in requested if rid in view])

    def _serve_custom_fields(
        self, path: str, _params: httpx.QueryParams
    ) -> httpx.Response:
        """Serve ``GET /v1/reservations-v3/{id}/custom-fields``."""
        reservation_id = path.split("/")[-2]
        if reservation_id in self.custom_fields:
            return httpx.Response(200, json=self.custom_fields[reservation_id])
        for item in self.v3_items:
            if item["reservationId"] == reservation_id:
                return httpx.Response(200, json=item.get("customFields") or [])
        return httpx.Response(200, json=[])

    def _guest(self, path: str) -> httpx.Response:
        """Serve one guest body or 404."""
        guest = self.guests.get(path.rsplit("/", 1)[-1])
        if guest is None:
            return httpx.Response(404, json={"error": "guest not found"})
        return httpx.Response(200, json=guest)

    def _serve_guests(self, path: str, _params: httpx.QueryParams) -> httpx.Response:
        """Serve legacy ``GET /v1/guests/{id}``."""
        return self._guest(path)

    def _serve_guests_crud(
        self, path: str, _params: httpx.QueryParams
    ) -> httpx.Response:
        """Serve ``GET /v1/guests-crud/{id}``."""
        return self._guest(path)

    def _serve_account(self, _path: str, _params: httpx.QueryParams) -> httpx.Response:
        """Serve ``GET /v1/accounts/me``."""
        return httpx.Response(200, json={"_id": "synthetic-account"})

    def _serve_custom_field_definitions(
        self, _path: str, _params: httpx.QueryParams
    ) -> httpx.Response:
        """Serve the (empty) custom-field definitions list."""
        return httpx.Response(200, json=[])


@pytest.fixture
def guesty_transport(guesty_v3_payloads: GuestyV3Payloads) -> GuestyFakeTransport:
    """A counting fake Guesty API sharing the payload builders (spec 004)."""
    return GuestyFakeTransport(guesty_v3_payloads)
