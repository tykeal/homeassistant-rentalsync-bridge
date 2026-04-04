# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for sync service."""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from src.database import Base
from src.models.booking import Booking
from src.models.listing import Listing
from src.providers.base import (
    PMSGuest,
    PMSProvider,
    PMSProviderError,
    PMSReservation,
)
from src.services.calendar_service import CalendarCache
from src.services.sync_service import SyncService, SyncServiceError


@pytest.fixture
async def sync_engine():
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
async def sync_session(sync_engine) -> AsyncGenerator[AsyncSession]:
    """Create test database session."""
    session_factory = async_sessionmaker(
        sync_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with session_factory() as session:
        yield session


@pytest.fixture
def sync_session_factory(sync_engine):
    """Create test session factory."""
    return async_sessionmaker(
        sync_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )


@pytest.fixture
def test_listing(sync_session):
    """Create test listing."""
    listing = Listing(
        pms_id="PROP123",
        name="Test Property",
        ical_url_slug="test-property",
        enabled=True,
        sync_enabled=True,
        timezone="America/New_York",
    )
    return listing


@pytest.fixture
def mock_provider():
    """Create mock PMSProvider."""
    provider = AsyncMock(spec=PMSProvider)
    provider.get_reservations = AsyncMock(return_value=[])
    provider.get_guest = AsyncMock(return_value=None)
    provider.get_custom_fields = AsyncMock(return_value={})
    provider.has_separate_custom_fields = False
    provider.provider_type = "cloudbeds"
    return provider


def _make_reservation(
    pms_booking_id: str = "RES001",
    listing_pms_id: str = "PROP123",
    guest_name: str | None = "John Smith",
    guest_id: str | None = None,
    check_in: datetime | None = None,
    check_out: datetime | None = None,
    status: str = "confirmed",
    room_ids: tuple[str, ...] = (),
    custom_data: dict | None = None,
) -> PMSReservation:
    """Build a PMSReservation DTO for tests."""
    return PMSReservation(
        pms_booking_id=pms_booking_id,
        listing_pms_id=listing_pms_id,
        guest_name=guest_name,
        guest_id=guest_id,
        check_in=check_in or datetime(2026, 3, 1, tzinfo=UTC),
        check_out=check_out or datetime(2026, 3, 5, tzinfo=UTC),
        status=status,
        room_ids=room_ids,
        custom_data=custom_data or {},
    )


class TestSyncService:
    """Tests for SyncService."""

    @pytest.mark.asyncio
    async def test_sync_disabled_listing(self, sync_session, mock_provider):
        """Test sync skips disabled listings."""
        listing = Listing(
            pms_id="DISABLED",
            name="Disabled Listing",
            ical_url_slug="disabled",
            enabled=True,
            sync_enabled=False,  # Sync disabled
        )
        sync_session.add(listing)
        await sync_session.commit()

        service = SyncService(sync_session)
        result = await service.sync_listing(listing, mock_provider)

        assert result == {"inserted": 0, "updated": 0, "cancelled": 0}

    @pytest.mark.asyncio
    async def test_sync_creates_new_bookings(
        self, sync_session, test_listing, mock_provider
    ):
        """Test sync creates new bookings from reservations."""
        sync_session.add(test_listing)
        await sync_session.commit()
        await sync_session.refresh(test_listing)

        mock_provider.get_reservations = AsyncMock(
            return_value=[
                _make_reservation(
                    pms_booking_id="RES001",
                    guest_name="John Smith",
                    custom_data={
                        "guest_phone_last4": "4567",
                    },
                ),
            ]
        )

        service = SyncService(sync_session)
        result = await service.sync_listing(test_listing, mock_provider)

        assert result["inserted"] == 1
        assert result["updated"] == 0
        assert result["cancelled"] == 0

    @pytest.mark.asyncio
    async def test_sync_updates_existing_bookings(
        self, sync_session, test_listing, mock_provider
    ):
        """Test sync updates existing bookings."""
        sync_session.add(test_listing)
        await sync_session.commit()
        await sync_session.refresh(test_listing)

        # Create existing booking
        booking = Booking(
            listing_id=test_listing.id,
            pms_booking_id="RES002",
            guest_name="Old Name",
            check_in_date=datetime(2026, 3, 1, tzinfo=UTC),
            check_out_date=datetime(2026, 3, 5, tzinfo=UTC),
            status="confirmed",
        )
        sync_session.add(booking)
        await sync_session.commit()

        mock_provider.get_reservations = AsyncMock(
            return_value=[
                _make_reservation(
                    pms_booking_id="RES002",
                    guest_name="New Name",
                ),
            ]
        )

        service = SyncService(sync_session)
        result = await service.sync_listing(test_listing, mock_provider)

        assert result["inserted"] == 0
        assert result["updated"] == 1
        assert result["cancelled"] == 0

    @pytest.mark.asyncio
    async def test_sync_marks_cancelled(
        self, sync_session, test_listing, mock_provider
    ):
        """Test sync marks bookings as cancelled when not in fetch."""
        sync_session.add(test_listing)
        await sync_session.commit()
        await sync_session.refresh(test_listing)

        booking = Booking(
            listing_id=test_listing.id,
            pms_booking_id="RES_GONE",
            guest_name="Cancelled Guest",
            check_in_date=datetime(2026, 3, 1, tzinfo=UTC),
            check_out_date=datetime(2026, 3, 5, tzinfo=UTC),
            status="confirmed",
        )
        sync_session.add(booking)
        await sync_session.commit()

        mock_provider.get_reservations = AsyncMock(return_value=[])

        service = SyncService(sync_session)
        result = await service.sync_listing(test_listing, mock_provider)

        assert result["inserted"] == 0
        assert result["updated"] == 0
        assert result["cancelled"] == 1

    @pytest.mark.asyncio
    async def test_sync_invalidates_cache(
        self, sync_session, test_listing, mock_provider
    ):
        """Test sync invalidates calendar cache when changes occur."""
        sync_session.add(test_listing)
        await sync_session.commit()
        await sync_session.refresh(test_listing)

        cache = CalendarCache()
        cache.set(test_listing.ical_url_slug, "cached_ical")

        mock_provider.get_reservations = AsyncMock(
            return_value=[
                _make_reservation(
                    pms_booking_id="RES003",
                    guest_name="New Guest",
                ),
            ]
        )

        service = SyncService(sync_session, calendar_cache=cache)
        await service.sync_listing(test_listing, mock_provider)

        # Cache should be invalidated
        assert cache.get(test_listing.ical_url_slug) is None

    @pytest.mark.asyncio
    async def test_sync_handles_api_error(
        self, sync_session, sync_session_factory, test_listing, mock_provider
    ):
        """Test sync raises error on API failure."""
        sync_session.add(test_listing)
        await sync_session.commit()

        mock_provider.get_reservations = AsyncMock(
            side_effect=PMSProviderError("API Error")
        )

        service = SyncService(sync_session, session_factory=sync_session_factory)
        with pytest.raises(SyncServiceError):
            await service.sync_listing(test_listing, mock_provider)


class TestExtractBookingDataFromDto:
    """Tests for booking data extraction from DTOs."""

    @pytest.fixture
    def service(self, sync_session):
        """Create sync service."""
        return SyncService(sync_session)

    def test_extract_guest_name(self, service):
        """Test extracting guest name from DTO."""
        reservation = _make_reservation(guest_name="Jane Doe")
        result = service._extract_booking_data_from_dto(reservation)

        assert result["guest_name"] == "Jane Doe"

    def test_extract_phone_last4_from_custom_data(self, service):
        """Test extracting phone last4 from custom_data."""
        reservation = _make_reservation(
            custom_data={"guest_phone_last4": "4567"},
        )
        result = service._extract_booking_data_from_dto(reservation)

        assert result["guest_phone_last4"] == "4567"

    def test_extract_status_normalization(self, service):
        """Test status from DTO is preserved."""
        reservation = _make_reservation(status="confirmed")
        result = service._extract_booking_data_from_dto(reservation)

        assert result["status"] == "confirmed"

    def test_extract_status_unknown_defaults_confirmed(self, service):
        """Test unknown status defaults to confirmed."""
        reservation = _make_reservation(status="UNKNOWN_STATUS")
        result = service._extract_booking_data_from_dto(reservation)

        assert result["status"] == "confirmed"

    def test_room_ids_from_dto(self, service):
        """Test room IDs come from DTO tuple."""
        reservation = _make_reservation(
            room_ids=("ROOM_A", "ROOM_B"),
        )
        result = service._extract_booking_data_from_dto(reservation)

        assert result["room_ids"] == ["ROOM_A", "ROOM_B"]


class TestExtractPhoneLast4:
    """Tests for phone number extraction."""

    def test_extract_from_full_phone(self):
        """Test extracting last 4 from full phone."""
        result = SyncService.extract_phone_last4("+1 (555) 123-4567")
        assert result == "4567"

    def test_extract_from_digits_only(self):
        """Test extracting from digits-only phone."""
        result = SyncService.extract_phone_last4("5551234567")
        assert result == "4567"

    def test_none_returns_none(self):
        """Test None input returns None."""
        result = SyncService.extract_phone_last4(None)
        assert result is None

    def test_short_number_returns_none(self):
        """Test short number returns None."""
        result = SyncService.extract_phone_last4("123")
        assert result is None

    def test_empty_returns_none(self):
        """Test empty string returns None."""
        result = SyncService.extract_phone_last4("")
        assert result is None


class TestListingIsolation:
    """Tests for listing isolation during sync."""

    @pytest.mark.asyncio
    async def test_sync_does_not_affect_other_listings(
        self, sync_session, mock_provider
    ):
        """Test syncing one listing doesn't affect other listings."""
        listing1 = Listing(
            pms_id="PROP_A",
            name="Property A",
            ical_url_slug="property-a",
            enabled=True,
            sync_enabled=True,
            timezone="America/New_York",
        )
        listing2 = Listing(
            pms_id="PROP_B",
            name="Property B",
            ical_url_slug="property-b",
            enabled=True,
            sync_enabled=True,
            timezone="America/Los_Angeles",
        )
        sync_session.add_all([listing1, listing2])
        await sync_session.commit()
        await sync_session.refresh(listing1)
        await sync_session.refresh(listing2)

        booking2 = Booking(
            listing_id=listing2.id,
            pms_booking_id="RES_B001",
            guest_name="Guest B",
            check_in_date=datetime(2026, 4, 1, tzinfo=UTC),
            check_out_date=datetime(2026, 4, 5, tzinfo=UTC),
            status="confirmed",
        )
        sync_session.add(booking2)
        await sync_session.commit()

        mock_provider.get_reservations = AsyncMock(return_value=[])

        service = SyncService(sync_session)
        result = await service.sync_listing(listing1, mock_provider)

        assert result["cancelled"] == 0

        await sync_session.refresh(booking2)
        assert booking2.status == "confirmed"

    @pytest.mark.asyncio
    async def test_sync_only_cancels_own_listing_bookings(
        self, sync_session, mock_provider
    ):
        """Test sync only cancels bookings belonging to that listing."""
        listing1 = Listing(
            pms_id="PROP_X",
            name="Property X",
            ical_url_slug="property-x",
            enabled=True,
            sync_enabled=True,
        )
        listing2 = Listing(
            pms_id="PROP_Y",
            name="Property Y",
            ical_url_slug="property-y",
            enabled=True,
            sync_enabled=True,
        )
        sync_session.add_all([listing1, listing2])
        await sync_session.commit()
        await sync_session.refresh(listing1)
        await sync_session.refresh(listing2)

        booking1 = Booking(
            listing_id=listing1.id,
            pms_booking_id="RES_X001",
            guest_name="Guest X",
            check_in_date=datetime(2026, 5, 1, tzinfo=UTC),
            check_out_date=datetime(2026, 5, 5, tzinfo=UTC),
            status="confirmed",
        )
        booking2 = Booking(
            listing_id=listing2.id,
            pms_booking_id="RES_Y001",
            guest_name="Guest Y",
            check_in_date=datetime(2026, 5, 10, tzinfo=UTC),
            check_out_date=datetime(2026, 5, 15, tzinfo=UTC),
            status="confirmed",
        )
        sync_session.add_all([booking1, booking2])
        await sync_session.commit()

        mock_provider.get_reservations = AsyncMock(return_value=[])

        service = SyncService(sync_session)
        result = await service.sync_listing(listing1, mock_provider)

        assert result["cancelled"] == 1

        await sync_session.refresh(booking1)
        await sync_session.refresh(booking2)
        assert booking1.status == "cancelled"
        assert booking2.status == "confirmed"


class TestSyncStatusTracking:
    """Tests for sync status tracking (last_sync_at, last_sync_error)."""

    @pytest.mark.asyncio
    async def test_sync_updates_last_sync_at_on_success(
        self, sync_session, mock_provider
    ):
        """Test that last_sync_at is updated on successful sync."""
        listing = Listing(
            pms_id="SYNC_STATUS",
            name="Sync Status Test",
            ical_url_slug="sync-status",
            enabled=True,
            sync_enabled=True,
            last_sync_at=None,
            last_sync_error=None,
        )
        sync_session.add(listing)
        await sync_session.commit()
        await sync_session.refresh(listing)

        assert listing.last_sync_at is None

        mock_provider.get_reservations = AsyncMock(return_value=[])

        service = SyncService(sync_session)
        result = await service.sync_listing(listing, mock_provider)

        assert result is not None
        assert listing.last_sync_at is not None
        assert listing.last_sync_error is None

    @pytest.mark.asyncio
    async def test_sync_clears_last_sync_error_on_success(
        self, sync_session, mock_provider
    ):
        """Test that last_sync_error is cleared on successful sync."""
        listing = Listing(
            pms_id="SYNC_ERROR_CLEAR",
            name="Sync Error Clear Test",
            ical_url_slug="sync-error-clear",
            enabled=True,
            sync_enabled=True,
            last_sync_at=None,
            last_sync_error="Previous error",
        )
        sync_session.add(listing)
        await sync_session.commit()
        await sync_session.refresh(listing)

        assert listing.last_sync_error == "Previous error"

        mock_provider.get_reservations = AsyncMock(return_value=[])

        service = SyncService(sync_session)
        result = await service.sync_listing(listing, mock_provider)

        assert result is not None
        assert listing.last_sync_error is None
        assert listing.last_sync_at is not None

    @pytest.mark.asyncio
    async def test_sync_updates_last_sync_error_on_failure(
        self, sync_session, sync_session_factory, mock_provider
    ):
        """Test that last_sync_error is set on failed sync."""
        listing = Listing(
            pms_id="SYNC_ERROR",
            name="Sync Error Test",
            ical_url_slug="sync-error",
            enabled=True,
            sync_enabled=True,
            last_sync_at=None,
            last_sync_error=None,
        )
        sync_session.add(listing)
        await sync_session.commit()
        await sync_session.refresh(listing)
        listing_id = listing.id

        assert listing.last_sync_error is None

        mock_provider.get_reservations = AsyncMock(
            side_effect=PMSProviderError("API rate limit exceeded")
        )

        service = SyncService(sync_session, session_factory=sync_session_factory)
        with pytest.raises(SyncServiceError):
            await service.sync_listing(listing, mock_provider)

        sync_session.expire_all()
        result = await sync_session.execute(
            select(Listing).where(Listing.id == listing_id)
        )
        refreshed_listing = result.scalar_one()
        assert refreshed_listing.last_sync_error == "API rate limit exceeded"
        assert refreshed_listing.last_sync_at is not None


class TestBookingChangeDetection:
    """Tests for booking change detection."""

    @pytest.mark.asyncio
    async def test_sync_returns_accurate_change_counts(
        self, sync_session, mock_provider
    ):
        """Test sync returns accurate counts for all change types."""
        listing = Listing(
            pms_id="CHANGE_DETECT",
            name="Change Detection Test",
            ical_url_slug="change-detect",
            enabled=True,
            sync_enabled=True,
        )
        sync_session.add(listing)
        await sync_session.commit()
        await sync_session.refresh(listing)

        existing_booking = Booking(
            listing_id=listing.id,
            pms_booking_id="EXISTING_001",
            guest_name="Existing Guest",
            check_in_date=datetime(2026, 1, 1, tzinfo=UTC),
            check_out_date=datetime(2026, 1, 5, tzinfo=UTC),
            status="confirmed",
        )
        sync_session.add(existing_booking)
        await sync_session.commit()

        mock_provider.get_reservations = AsyncMock(
            return_value=[
                _make_reservation(
                    pms_booking_id="NEW_001",
                    guest_name="New Guest",
                    check_in=datetime(2026, 2, 1, tzinfo=UTC),
                    check_out=datetime(2026, 2, 5, tzinfo=UTC),
                ),
            ]
        )

        service = SyncService(sync_session)
        result = await service.sync_listing(listing, mock_provider)

        assert result["inserted"] == 1
        assert result["updated"] == 0
        assert result["cancelled"] == 1


class TestInvalidDateHandling:
    """Tests for handling reservations with edge-case dates."""

    @pytest.mark.asyncio
    async def test_sync_processes_valid_reservation(self, sync_session, mock_provider):
        """Test valid reservation is processed correctly."""
        listing = Listing(
            pms_id="VALID_DATE",
            name="Valid Date Test",
            ical_url_slug="valid-date",
            enabled=True,
            sync_enabled=True,
        )
        sync_session.add(listing)
        await sync_session.commit()

        mock_provider.get_reservations = AsyncMock(
            return_value=[
                _make_reservation(
                    pms_booking_id="VALID_001",
                    guest_name="Valid Guest",
                    check_in=datetime(2026, 2, 1, tzinfo=UTC),
                    check_out=datetime(2026, 2, 5, tzinfo=UTC),
                ),
            ]
        )

        service = SyncService(sync_session)
        result = await service.sync_listing(listing, mock_provider)

        assert result["inserted"] == 1

    @pytest.mark.asyncio
    async def test_sync_skips_reservation_with_empty_id(
        self, sync_session, mock_provider
    ):
        """Test reservations with empty booking ID are skipped."""
        listing = Listing(
            pms_id="EMPTY_ID",
            name="Empty ID Test",
            ical_url_slug="empty-id",
            enabled=True,
            sync_enabled=True,
        )
        sync_session.add(listing)
        await sync_session.commit()

        mock_provider.get_reservations = AsyncMock(
            return_value=[
                _make_reservation(
                    pms_booking_id="",
                    guest_name="No ID Guest",
                ),
            ]
        )

        service = SyncService(sync_session)
        result = await service.sync_listing(listing, mock_provider)

        assert result["inserted"] == 0


class TestRoomAssociation:
    """Tests for booking room association during sync."""

    @pytest.mark.asyncio
    async def test_sync_associates_booking_with_room(self, sync_session, mock_provider):
        """Test sync associates booking with room."""
        from src.models.room import Room

        listing = Listing(
            pms_id="ROOM_TEST",
            name="Room Test Property",
            ical_url_slug="room-test",
            enabled=True,
            sync_enabled=True,
        )
        sync_session.add(listing)
        await sync_session.commit()
        await sync_session.refresh(listing)

        room = Room(
            listing_id=listing.id,
            pms_room_id="ROOM_123",
            room_name="Suite 101",
            ical_url_slug="suite-101",
            enabled=True,
        )
        sync_session.add(room)
        await sync_session.commit()
        await sync_session.refresh(room)

        mock_provider.get_reservations = AsyncMock(
            return_value=[
                _make_reservation(
                    pms_booking_id="RES_WITH_ROOM",
                    guest_name="Room Guest",
                    room_ids=("ROOM_123",),
                ),
            ]
        )

        service = SyncService(sync_session)
        result = await service.sync_listing(listing, mock_provider)

        assert result["inserted"] == 1

        stmt = select(Booking).where(
            Booking.pms_booking_id == "RES_WITH_ROOM::ROOM_123"
        )
        db_result = await sync_session.execute(stmt)
        booking = db_result.scalar_one()
        assert booking.room_id == room.id

    @pytest.mark.asyncio
    async def test_sync_booking_without_room_id(self, sync_session, mock_provider):
        """Test sync handles bookings without room IDs."""
        listing = Listing(
            pms_id="NO_ROOM_TEST",
            name="No Room Test",
            ical_url_slug="no-room-test",
            enabled=True,
            sync_enabled=True,
        )
        sync_session.add(listing)
        await sync_session.commit()
        await sync_session.refresh(listing)

        mock_provider.get_reservations = AsyncMock(
            return_value=[
                _make_reservation(
                    pms_booking_id="RES_NO_ROOM",
                    guest_name="No Room Guest",
                    room_ids=(),
                ),
            ]
        )

        service = SyncService(sync_session)
        result = await service.sync_listing(listing, mock_provider)

        assert result["inserted"] == 1

        stmt = select(Booking).where(Booking.pms_booking_id == "RES_NO_ROOM")
        db_result = await sync_session.execute(stmt)
        booking = db_result.scalar_one()
        assert booking.room_id is None

    @pytest.mark.asyncio
    async def test_sync_booking_with_unknown_room_id(self, sync_session, mock_provider):
        """Test sync handles bookings with unknown room ID."""
        listing = Listing(
            pms_id="UNKNOWN_ROOM",
            name="Unknown Room Test",
            ical_url_slug="unknown-room",
            enabled=True,
            sync_enabled=True,
        )
        sync_session.add(listing)
        await sync_session.commit()
        await sync_session.refresh(listing)

        mock_provider.get_reservations = AsyncMock(
            return_value=[
                _make_reservation(
                    pms_booking_id="RES_UNKNOWN_ROOM",
                    guest_name="Unknown Room Guest",
                    room_ids=("NONEXISTENT_ROOM",),
                ),
            ]
        )

        service = SyncService(sync_session)
        result = await service.sync_listing(listing, mock_provider)

        assert result["inserted"] == 1

        stmt = select(Booking).where(
            Booking.pms_booking_id == "RES_UNKNOWN_ROOM::NONEXISTENT_ROOM"
        )
        db_result = await sync_session.execute(stmt)
        booking = db_result.scalar_one()
        assert booking.room_id is None

    @pytest.mark.asyncio
    async def test_sync_updates_room_id_on_room_change(
        self, sync_session, mock_provider
    ):
        """Test sync handles booking moved to different room."""
        from src.models.room import Room

        listing = Listing(
            pms_id="ROOM_CHANGE_TEST",
            name="Room Change Test",
            ical_url_slug="room-change-test",
            enabled=True,
            sync_enabled=True,
        )
        sync_session.add(listing)
        await sync_session.commit()
        await sync_session.refresh(listing)

        room1 = Room(
            listing_id=listing.id,
            pms_room_id="ROOM_A",
            room_name="Room A",
            ical_url_slug="room-a",
            enabled=True,
        )
        room2 = Room(
            listing_id=listing.id,
            pms_room_id="ROOM_B",
            room_name="Room B",
            ical_url_slug="room-b",
            enabled=True,
        )
        sync_session.add_all([room1, room2])
        await sync_session.commit()
        await sync_session.refresh(room1)
        await sync_session.refresh(room2)

        existing_booking = Booking(
            listing_id=listing.id,
            room_id=room1.id,
            pms_booking_id="RES_ROOM_CHANGE::ROOM_A",
            guest_name="Moving Guest",
            check_in_date=datetime(2026, 3, 1, tzinfo=UTC),
            check_out_date=datetime(2026, 3, 5, tzinfo=UTC),
            status="confirmed",
        )
        sync_session.add(existing_booking)
        await sync_session.commit()

        mock_provider.get_reservations = AsyncMock(
            return_value=[
                _make_reservation(
                    pms_booking_id="RES_ROOM_CHANGE",
                    guest_name="Moving Guest",
                    room_ids=("ROOM_B",),
                ),
            ]
        )

        service = SyncService(sync_session)
        result = await service.sync_listing(listing, mock_provider)

        assert result["inserted"] == 1
        assert result["cancelled"] == 1

        await sync_session.refresh(existing_booking)
        assert existing_booking.status == "cancelled"

        stmt = select(Booking).where(
            Booking.pms_booking_id == "RES_ROOM_CHANGE::ROOM_B"
        )
        db_result = await sync_session.execute(stmt)
        new_booking = db_result.scalar_one()
        assert new_booking.room_id == room2.id

    @pytest.mark.asyncio
    async def test_sync_extracts_room_id_from_dto_room_ids(
        self, sync_session, mock_provider
    ):
        """Test sync extracts roomID from DTO room_ids."""
        from src.models.room import Room

        listing = Listing(
            pms_id="NESTED_ROOM_TEST",
            name="Nested Room Test Property",
            ical_url_slug="nested-room-test",
            enabled=True,
            sync_enabled=True,
        )
        sync_session.add(listing)
        await sync_session.commit()
        await sync_session.refresh(listing)

        room = Room(
            listing_id=listing.id,
            pms_room_id="662541-0",
            room_name="Suite 01",
            ical_url_slug="suite-01",
            enabled=True,
        )
        sync_session.add(room)
        await sync_session.commit()
        await sync_session.refresh(room)

        mock_provider.get_reservations = AsyncMock(
            return_value=[
                _make_reservation(
                    pms_booking_id="RES_NESTED_ROOM",
                    guest_name="Nested Room Guest",
                    room_ids=("662541-0",),
                ),
            ]
        )

        service = SyncService(sync_session)
        result = await service.sync_listing(listing, mock_provider)

        assert result["inserted"] == 1

        stmt = select(Booking).where(
            Booking.pms_booking_id == "RES_NESTED_ROOM::662541-0"
        )
        db_result = await sync_session.execute(stmt)
        booking = db_result.scalar_one()
        assert booking.room_id == room.id

    @pytest.mark.asyncio
    async def test_sync_creates_booking_per_room_for_multi_room(
        self, sync_session, mock_provider
    ):
        """Test multi-room reservations create a booking per room."""
        from src.models.room import Room

        listing = Listing(
            pms_id="PROP_MULTI_ROOM",
            name="Multi Room Property",
            ical_url_slug="multi-room-prop",
            timezone="UTC",
            enabled=True,
            sync_enabled=True,
        )
        sync_session.add(listing)
        await sync_session.commit()
        await sync_session.refresh(listing)

        room1 = Room(
            listing_id=listing.id,
            pms_room_id="100-0",
            room_name="Room A",
            ical_url_slug="room-a",
            enabled=True,
        )
        room2 = Room(
            listing_id=listing.id,
            pms_room_id="100-1",
            room_name="Room B",
            ical_url_slug="room-b",
            enabled=True,
        )
        sync_session.add_all([room1, room2])
        await sync_session.commit()
        await sync_session.refresh(room1)
        await sync_session.refresh(room2)

        mock_provider.get_reservations = AsyncMock(
            return_value=[
                _make_reservation(
                    pms_booking_id="RES_MULTI_ROOM",
                    guest_name="Multi Room Guest",
                    check_in=datetime(2026, 4, 1, tzinfo=UTC),
                    check_out=datetime(2026, 4, 5, tzinfo=UTC),
                    room_ids=("100-0", "100-1"),
                ),
            ]
        )

        service = SyncService(sync_session)
        result = await service.sync_listing(listing, mock_provider)

        assert result["inserted"] == 2

        stmt = select(Booking).where(
            Booking.listing_id == listing.id,
        )
        db_result = await sync_session.execute(stmt)
        bookings = db_result.scalars().all()

        assert len(bookings) == 2
        booking_ids = {b.pms_booking_id for b in bookings}
        assert booking_ids == {
            "RES_MULTI_ROOM::100-0",
            "RES_MULTI_ROOM::100-1",
        }
        room_ids = {b.room_id for b in bookings}
        assert room_ids == {room1.id, room2.id}

    @pytest.mark.asyncio
    async def test_sync_handles_room_count_transition(
        self, sync_session, mock_provider
    ):
        """Test changing room count properly handles bookings."""
        from src.models.room import Room

        listing = Listing(
            pms_id="PROP_TRANSITION",
            name="Transition Property",
            ical_url_slug="transition-prop",
            timezone="UTC",
            enabled=True,
            sync_enabled=True,
        )
        sync_session.add(listing)
        await sync_session.commit()
        await sync_session.refresh(listing)

        room1 = Room(
            listing_id=listing.id,
            pms_room_id="200-0",
            room_name="Room X",
            ical_url_slug="room-x",
            enabled=True,
        )
        room2 = Room(
            listing_id=listing.id,
            pms_room_id="200-1",
            room_name="Room Y",
            ical_url_slug="room-y",
            enabled=True,
        )
        sync_session.add_all([room1, room2])
        await sync_session.commit()
        await sync_session.refresh(room1)
        await sync_session.refresh(room2)

        # First sync: single-room reservation
        mock_provider.get_reservations = AsyncMock(
            return_value=[
                _make_reservation(
                    pms_booking_id="RES_TRANSITION",
                    guest_name="Test Guest",
                    check_in=datetime(2026, 5, 1, tzinfo=UTC),
                    check_out=datetime(2026, 5, 5, tzinfo=UTC),
                    room_ids=("200-0",),
                ),
            ]
        )

        service = SyncService(sync_session)
        result = await service.sync_listing(listing, mock_provider)

        assert result["inserted"] == 1

        # Second sync: same reservation now spans TWO rooms
        mock_provider.get_reservations = AsyncMock(
            return_value=[
                _make_reservation(
                    pms_booking_id="RES_TRANSITION",
                    guest_name="Test Guest",
                    check_in=datetime(2026, 5, 1, tzinfo=UTC),
                    check_out=datetime(2026, 5, 5, tzinfo=UTC),
                    room_ids=("200-0", "200-1"),
                ),
            ]
        )

        service = SyncService(sync_session)
        result = await service.sync_listing(listing, mock_provider)

        assert result["inserted"] == 1
        assert result["updated"] == 1
        assert result["cancelled"] == 0

        stmt = select(Booking).where(
            Booking.listing_id == listing.id,
        )
        db_result = await sync_session.execute(stmt)
        bookings = db_result.scalars().all()

        assert len(bookings) == 2
        active = [b for b in bookings if b.status != "cancelled"]
        assert len(active) == 2

        booking_ids = {b.pms_booking_id for b in bookings}
        assert booking_ids == {
            "RES_TRANSITION::200-0",
            "RES_TRANSITION::200-1",
        }


class TestGuestResolution:
    """Tests for provider-agnostic guest name resolution."""

    @pytest.mark.asyncio
    async def test_resolve_guest_name_from_provider(self, sync_session, mock_provider):
        """Test guest name is resolved via provider.get_guest()."""
        listing = Listing(
            pms_id="GUEST_RESOLVE",
            name="Guest Resolve Test",
            ical_url_slug="guest-resolve",
            enabled=True,
            sync_enabled=True,
        )
        sync_session.add(listing)
        await sync_session.commit()
        await sync_session.refresh(listing)

        mock_provider.get_reservations = AsyncMock(
            return_value=[
                _make_reservation(
                    pms_booking_id="RES_GUEST",
                    guest_name=None,
                    guest_id="G123",
                ),
            ]
        )
        mock_provider.get_guest = AsyncMock(
            return_value=PMSGuest(
                guest_id="G123",
                full_name="Resolved Name",
                phone="+1-555-999-1234",
                email="resolved@example.com",
            )
        )

        service = SyncService(sync_session)
        result = await service.sync_listing(listing, mock_provider)

        assert result["inserted"] == 1

        stmt = select(Booking).where(
            Booking.pms_booking_id == "RES_GUEST",
        )
        db_result = await sync_session.execute(stmt)
        booking = db_result.scalar_one()
        assert booking.guest_name == "Resolved Name"
        assert booking.guest_phone_last4 == "1234"

    @pytest.mark.asyncio
    async def test_inline_guest_name_not_overridden(self, sync_session, mock_provider):
        """Test inline guest_name is preserved after enrichment."""
        listing = Listing(
            pms_id="GUEST_INLINE",
            name="Guest Inline Test",
            ical_url_slug="guest-inline",
            enabled=True,
            sync_enabled=True,
        )
        sync_session.add(listing)
        await sync_session.commit()
        await sync_session.refresh(listing)

        mock_provider.get_reservations = AsyncMock(
            return_value=[
                _make_reservation(
                    pms_booking_id="RES_INLINE",
                    guest_name="Inline Name",
                    guest_id="G456",
                ),
            ]
        )
        mock_provider.get_guest = AsyncMock(
            return_value=PMSGuest(
                guest_id="G456",
                full_name="API Name",
                phone="+15551110000",
                email="inline@example.com",
            )
        )

        service = SyncService(sync_session)
        result = await service.sync_listing(listing, mock_provider)

        assert result["inserted"] == 1
        # get_guest IS called for enrichment
        mock_provider.get_guest.assert_called_once_with("G456")

        stmt = select(Booking).where(
            Booking.pms_booking_id == "RES_INLINE",
        )
        db_result = await sync_session.execute(stmt)
        booking = db_result.scalar_one()
        # Inline name kept, not overwritten by API name
        assert booking.guest_name == "Inline Name"
        # Phone and email enriched from get_guest()
        assert booking.guest_phone_last4 == "0000"
        assert booking.custom_data["guest_phone"] == "+15551110000"
        assert booking.custom_data["guest_email"] == "inline@example.com"

    @pytest.mark.asyncio
    async def test_guest_resolution_batches_unique_ids(
        self, sync_session, mock_provider
    ):
        """Test guest resolution batches unique guest_ids."""
        listing = Listing(
            pms_id="GUEST_BATCH",
            name="Guest Batch Test",
            ical_url_slug="guest-batch",
            enabled=True,
            sync_enabled=True,
        )
        sync_session.add(listing)
        await sync_session.commit()
        await sync_session.refresh(listing)

        mock_provider.get_reservations = AsyncMock(
            return_value=[
                _make_reservation(
                    pms_booking_id="RES_A",
                    guest_name=None,
                    guest_id="G100",
                    check_in=datetime(2026, 3, 1, tzinfo=UTC),
                    check_out=datetime(2026, 3, 5, tzinfo=UTC),
                ),
                _make_reservation(
                    pms_booking_id="RES_B",
                    guest_name=None,
                    guest_id="G100",  # Same guest
                    check_in=datetime(2026, 4, 1, tzinfo=UTC),
                    check_out=datetime(2026, 4, 5, tzinfo=UTC),
                ),
            ]
        )
        mock_provider.get_guest = AsyncMock(
            return_value=PMSGuest(
                guest_id="G100",
                full_name="Batch Guest",
            )
        )

        service = SyncService(sync_session)
        await service.sync_listing(listing, mock_provider)

        # Should call get_guest only once for the unique ID
        assert mock_provider.get_guest.call_count == 1

    @pytest.mark.asyncio
    async def test_phone_last4_extraction(self, sync_session):
        """Test phone last 4 digit extraction."""
        assert SyncService.extract_phone_last4("+1-555-1234") == "1234"
        assert SyncService.extract_phone_last4("5551234567") == "4567"
        assert SyncService.extract_phone_last4(None) is None
        assert SyncService.extract_phone_last4("") is None
        assert SyncService.extract_phone_last4("12") is None

    @pytest.mark.asyncio
    async def test_guest_email_added_to_custom_data(self, sync_session, mock_provider):
        """Test guest email is added to custom_data during resolution."""
        listing = Listing(
            pms_id="GUEST_EMAIL",
            name="Guest Email Test",
            ical_url_slug="guest-email",
            enabled=True,
            sync_enabled=True,
        )
        sync_session.add(listing)
        await sync_session.commit()
        await sync_session.refresh(listing)

        mock_provider.get_reservations = AsyncMock(
            return_value=[
                _make_reservation(
                    pms_booking_id="RES_EMAIL",
                    guest_name=None,
                    guest_id="GE1",
                ),
            ]
        )
        mock_provider.get_guest = AsyncMock(
            return_value=PMSGuest(
                guest_id="GE1",
                full_name="Email Guest",
                phone="+15559990000",
                email="guest@example.com",
            )
        )

        service = SyncService(sync_session)
        reservations = [
            _make_reservation(
                pms_booking_id="RES_EMAIL",
                guest_name=None,
                guest_id="GE1",
            ),
        ]
        resolved = await service._resolve_guest_names(mock_provider, reservations)

        r = resolved[0]
        assert r.custom_data["guest_email"] == "guest@example.com"
        assert r.custom_data["guest_phone"] == "+15559990000"
        assert r.custom_data["guest_phone_last4"] == "0000"

    @pytest.mark.asyncio
    async def test_enrichment_with_existing_name(self, sync_session, mock_provider):
        """Enrichment adds phone/email even when name is present."""
        service = SyncService(sync_session)
        mock_provider.get_guest = AsyncMock(
            return_value=PMSGuest(
                guest_id="G1",
                full_name="API Name",
                phone="+15551234567",
                email="alice@example.com",
            )
        )
        reservations = [
            _make_reservation(
                pms_booking_id="RES_NAMED",
                guest_name="Alice",
                guest_id="G1",
            ),
        ]
        resolved = await service._resolve_guest_names(mock_provider, reservations)

        r = resolved[0]
        # Original name kept
        assert r.guest_name == "Alice"
        # Phone and email enriched
        assert r.custom_data["guest_phone"] == "+15551234567"
        assert r.custom_data["guest_email"] == "alice@example.com"
        assert r.custom_data["guest_phone_last4"] == "4567"
        mock_provider.get_guest.assert_called_once_with("G1")

    @pytest.mark.asyncio
    async def test_no_guest_id_skips_enrichment(self, sync_session, mock_provider):
        """Reservation without guest_id is returned unchanged."""
        service = SyncService(sync_session)
        reservations = [
            _make_reservation(
                pms_booking_id="RES_NOID",
                guest_name="Bob",
                guest_id=None,
            ),
        ]
        resolved = await service._resolve_guest_names(mock_provider, reservations)

        r = resolved[0]
        assert r.guest_name == "Bob"
        assert "guest_phone" not in r.custom_data
        assert "guest_email" not in r.custom_data
        mock_provider.get_guest.assert_not_called()

    @pytest.mark.asyncio
    async def test_already_enriched_skips_get_guest(self, sync_session, mock_provider):
        """Skip get_guest() when phone and email already present."""
        service = SyncService(sync_session)
        reservations = [
            _make_reservation(
                pms_booking_id="RES_FULL",
                guest_name="Carol",
                guest_id="G9",
                custom_data={
                    "guest_phone": "+15550001111",
                    "guest_email": "carol@example.com",
                },
            ),
        ]
        resolved = await service._resolve_guest_names(mock_provider, reservations)

        r = resolved[0]
        assert r.guest_name == "Carol"
        assert r.custom_data["guest_phone"] == "+15550001111"
        assert r.custom_data["guest_email"] == "carol@example.com"
        mock_provider.get_guest.assert_not_called()

    @pytest.mark.asyncio
    async def test_enrichment_preserves_existing_custom_data(
        self, sync_session, mock_provider
    ):
        """Existing custom_data values are not overwritten."""
        service = SyncService(sync_session)
        mock_provider.get_guest = AsyncMock(
            return_value=PMSGuest(
                guest_id="G7",
                full_name="API Name",
                phone="+15559999999",
                email="api@example.com",
            )
        )
        reservations = [
            _make_reservation(
                pms_booking_id="RES_PARTIAL",
                guest_name="Dave",
                guest_id="G7",
                custom_data={"guest_phone": "+15550001111"},
            ),
        ]
        resolved = await service._resolve_guest_names(mock_provider, reservations)

        r = resolved[0]
        # Existing phone kept, API email filled in
        assert r.custom_data["guest_phone"] == "+15550001111"
        assert r.custom_data["guest_email"] == "api@example.com"


class TestCustomFieldEnrichment:
    """Tests for custom field enrichment step."""

    @pytest.mark.asyncio
    async def test_enrichment_merges_custom_fields(self, sync_session, mock_provider):
        """Test custom fields are merged into custom_data."""
        mock_provider.has_separate_custom_fields = True
        mock_provider.get_custom_fields = AsyncMock(
            return_value={"cf_1": "val1", "cf_2": "val2", "source": "api"},
        )

        service = SyncService(sync_session)
        reservations = [
            _make_reservation(
                pms_booking_id="RES_CF",
                custom_data={"source": "airbnb"},
            ),
        ]
        enriched = await service._enrich_custom_fields(mock_provider, reservations)

        assert len(enriched) == 1
        assert enriched[0].custom_data["cf_1"] == "val1"
        assert enriched[0].custom_data["cf_2"] == "val2"
        # Existing custom_data wins on key conflicts
        assert enriched[0].custom_data["source"] == "airbnb"

    @pytest.mark.asyncio
    async def test_enrichment_noop_when_empty(self, sync_session, mock_provider):
        """Test enrichment is a no-op when provider returns empty."""
        mock_provider.has_separate_custom_fields = True
        mock_provider.get_custom_fields = AsyncMock(return_value={})

        service = SyncService(sync_session)
        reservations = [
            _make_reservation(
                pms_booking_id="RES_NOOP",
                custom_data={"existing": "data"},
            ),
        ]
        enriched = await service._enrich_custom_fields(mock_provider, reservations)

        assert enriched[0].custom_data == {"existing": "data"}

    @pytest.mark.asyncio
    async def test_enrichment_handles_provider_error(self, sync_session, mock_provider):
        """Test enrichment handles provider errors gracefully."""
        mock_provider.has_separate_custom_fields = True
        mock_provider.get_custom_fields = AsyncMock(
            side_effect=PMSProviderError("API error")
        )

        service = SyncService(sync_session)
        reservations = [
            _make_reservation(pms_booking_id="RES_ERR"),
        ]
        enriched = await service._enrich_custom_fields(mock_provider, reservations)

        assert len(enriched) == 1
        assert enriched[0].pms_booking_id == "RES_ERR"

    @pytest.mark.asyncio
    async def test_enrichment_skipped_when_not_supported(
        self, sync_session, mock_provider
    ):
        """Test enrichment is skipped for providers without separate fields."""
        mock_provider.has_separate_custom_fields = False

        service = SyncService(sync_session)
        reservations = [
            _make_reservation(pms_booking_id="RES_SKIP"),
        ]
        enriched = await service._enrich_custom_fields(mock_provider, reservations)

        assert enriched is reservations
        mock_provider.get_custom_fields.assert_not_called()
