# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for sync service."""

import logging
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from src.database import Base
from src.models.available_field import AvailableField
from src.models.booking import OLD_BOOKING_RETENTION_DAYS, Booking
from src.models.custom_field import CustomField
from src.models.listing import Listing
from src.providers.base import (
    PMSGuest,
    PMSProvider,
    PMSProviderError,
    PMSReservation,
)
from src.repositories.available_field_repository import AvailableFieldRepository
from src.repositories.booking_repository import BookingRepository
from src.repositories.custom_field_repository import CustomFieldRepository
from src.services.calendar_service import CalendarCache
from src.services.sync_service import (
    GUEST_SOURCE_ID_KEY,
    SyncService,
    SyncServiceError,
)


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


def _test_date(day_offset: int) -> datetime:
    """Build a test datetime relative to now.

    Fixture dates must stay inside the sync retention window, so they are
    anchored to the current time instead of hard-coded calendar dates.

    Args:
        day_offset: Days from now.

    Returns:
        Timezone-aware datetime in UTC.
    """
    return datetime.now(UTC).replace(microsecond=0) + timedelta(days=day_offset)


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
    updated_at: datetime | None = None,
) -> PMSReservation:
    """Build a PMSReservation DTO for tests."""
    return PMSReservation(
        pms_booking_id=pms_booking_id,
        listing_pms_id=listing_pms_id,
        guest_name=guest_name,
        guest_id=guest_id,
        check_in=check_in or _test_date(61),
        check_out=check_out or _test_date(65),
        status=status,
        room_ids=room_ids,
        custom_data=custom_data or {},
        updated_at=updated_at,
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
            check_in_date=_test_date(61),
            check_out_date=_test_date(65),
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
            check_in_date=_test_date(61),
            check_out_date=_test_date(65),
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
    @pytest.mark.parametrize("status", ["confirmed", "checked_in"])
    async def test_sync_keeps_in_progress_booking(
        self, sync_session, test_listing, mock_provider, status
    ):
        """Test in-progress bookings survive absence from the fetch."""
        sync_session.add(test_listing)
        await sync_session.commit()
        await sync_session.refresh(test_listing)

        now = datetime.now(UTC)
        booking = Booking(
            listing_id=test_listing.id,
            pms_booking_id="RES_CURRENT",
            guest_name="Current Guest",
            check_in_date=now - timedelta(days=3),
            check_out_date=now + timedelta(days=2),
            status=status,
        )
        sync_session.add(booking)
        await sync_session.commit()

        mock_provider.get_reservations = AsyncMock(return_value=[])

        service = SyncService(sync_session)
        result = await service.sync_listing(test_listing, mock_provider)

        assert result["cancelled"] == 0
        await sync_session.refresh(booking)
        assert booking.status == status

    @pytest.mark.asyncio
    async def test_sync_cancels_future_booking_absent_from_fetch(
        self, sync_session, test_listing, mock_provider
    ):
        """Test future bookings are still cancelled when absent."""
        sync_session.add(test_listing)
        await sync_session.commit()
        await sync_session.refresh(test_listing)

        now = datetime.now(UTC)
        booking = Booking(
            listing_id=test_listing.id,
            pms_booking_id="RES_FUTURE",
            guest_name="Future Guest",
            check_in_date=now + timedelta(days=10),
            check_out_date=now + timedelta(days=14),
            status="confirmed",
        )
        sync_session.add(booking)
        await sync_session.commit()

        mock_provider.get_reservations = AsyncMock(return_value=[])

        service = SyncService(sync_session)
        result = await service.sync_listing(test_listing, mock_provider)

        assert result["cancelled"] == 1
        await sync_session.refresh(booking)
        assert booking.status == "cancelled"

    @pytest.mark.asyncio
    async def test_sync_cancels_past_booking_absent_from_fetch(
        self, sync_session, test_listing, mock_provider
    ):
        """Test past bookings are still cancelled when absent."""
        sync_session.add(test_listing)
        await sync_session.commit()
        await sync_session.refresh(test_listing)

        now = datetime.now(UTC)
        booking = Booking(
            listing_id=test_listing.id,
            pms_booking_id="RES_PAST",
            guest_name="Past Guest",
            check_in_date=now - timedelta(days=10),
            check_out_date=now - timedelta(days=5),
            status="confirmed",
        )
        sync_session.add(booking)
        await sync_session.commit()

        mock_provider.get_reservations = AsyncMock(return_value=[])

        service = SyncService(sync_session)
        result = await service.sync_listing(test_listing, mock_provider)

        assert result["cancelled"] == 1
        await sync_session.refresh(booking)
        assert booking.status == "cancelled"

    @pytest.mark.asyncio
    async def test_sync_cancels_in_progress_booking_from_fetch(
        self, sync_session, test_listing, mock_provider
    ):
        """Test a cancellation during the stay is not shielded as in-progress."""
        sync_session.add(test_listing)
        await sync_session.commit()
        await sync_session.refresh(test_listing)

        now = datetime.now(UTC)
        check_in = now - timedelta(days=1)
        check_out = now + timedelta(days=2)
        booking = Booking(
            listing_id=test_listing.id,
            pms_booking_id="RES_CANCELLED",
            guest_name="Cancelled Guest",
            check_in_date=check_in,
            check_out_date=check_out,
            status="checked_in",
        )
        sync_session.add(booking)
        await sync_session.commit()

        mock_provider.get_reservations = AsyncMock(
            return_value=[
                _make_reservation(
                    pms_booking_id="RES_CANCELLED",
                    check_in=check_in,
                    check_out=check_out,
                    status="cancelled",
                )
            ]
        )

        service = SyncService(sync_session)
        await service.sync_listing(test_listing, mock_provider)

        await sync_session.refresh(booking)
        assert booking.status == "cancelled"

    @pytest.mark.asyncio
    async def test_sync_retires_no_show_during_stay(
        self, sync_session, test_listing, mock_provider
    ):
        """Test a no-show during the stay is retired, not kept in progress."""
        from src.providers.cloudbeds.provider import CloudbedsProvider

        sync_session.add(test_listing)
        await sync_session.commit()
        await sync_session.refresh(test_listing)

        now = datetime.now(UTC)
        check_in = now - timedelta(days=1)
        check_out = now + timedelta(days=2)
        booking = Booking(
            listing_id=test_listing.id,
            pms_booking_id="RES_NO_SHOW",
            guest_name="Absent Guest",
            check_in_date=check_in,
            check_out_date=check_out,
            status="confirmed",
        )
        sync_session.add(booking)
        await sync_session.commit()

        # Build the DTO through the real Cloudbeds mapping so the regression
        # covers the raw "no_show" status, not a pre-normalized value.
        reservation = CloudbedsProvider._map_reservation(
            {
                "reservationID": "RES_NO_SHOW",
                "guestName": "Absent Guest",
                "startDate": check_in.isoformat(),
                "endDate": check_out.isoformat(),
                "status": "no_show",
            },
            test_listing.pms_id,
        )
        assert reservation.status == "cancelled"

        mock_provider.get_reservations = AsyncMock(return_value=[reservation])

        service = SyncService(sync_session)
        result = await service.sync_listing(test_listing, mock_provider)

        assert result["cancelled"] == 1
        await sync_session.refresh(booking)
        assert booking.status == "cancelled"

    @pytest.mark.asyncio
    async def test_sync_does_not_insert_unknown_cancellation(
        self, sync_session, test_listing, mock_provider
    ):
        """Test cancellations with no stored booking are never inserted."""
        sync_session.add(test_listing)
        await sync_session.commit()
        await sync_session.refresh(test_listing)

        mock_provider.get_reservations = AsyncMock(
            return_value=[
                _make_reservation(
                    pms_booking_id="RES_PURGED_CANCEL",
                    status="cancelled",
                )
            ]
        )

        service = SyncService(sync_session)
        result = await service.sync_listing(test_listing, mock_provider)

        assert result["inserted"] == 0
        assert result["cancelled"] == 0
        stored = await sync_session.execute(
            select(Booking).where(Booking.pms_booking_id == "RES_PURGED_CANCEL")
        )
        assert stored.scalar_one_or_none() is None

    @pytest.mark.asyncio
    async def test_sync_does_not_retouch_cancelled_booking(
        self, sync_session, test_listing, mock_provider
    ):
        """Test repeated cancellations keep updated_at pinned."""
        sync_session.add(test_listing)
        await sync_session.commit()
        await sync_session.refresh(test_listing)

        booking = Booking(
            listing_id=test_listing.id,
            pms_booking_id="RES_STALE_CANCEL",
            guest_name="Cancelled Guest",
            check_in_date=_test_date(-5),
            check_out_date=_test_date(-2),
            status="cancelled",
        )
        sync_session.add(booking)
        await sync_session.commit()
        await sync_session.refresh(booking)
        original_updated_at = booking.updated_at

        mock_provider.get_reservations = AsyncMock(
            return_value=[
                _make_reservation(
                    pms_booking_id="RES_STALE_CANCEL",
                    check_in=_test_date(-5),
                    check_out=_test_date(-2),
                    status="cancelled",
                )
            ]
        )

        service = SyncService(sync_session)
        for _ in range(2):
            result = await service.sync_listing(test_listing, mock_provider)
            assert result == {"inserted": 0, "updated": 0, "cancelled": 0}

        await sync_session.refresh(booking)
        assert booking.status == "cancelled"
        assert booking.updated_at == original_updated_at

    @pytest.mark.asyncio
    async def test_sync_cancels_active_booking_only_once(
        self, sync_session, test_listing, mock_provider
    ):
        """Test an active booking is transitioned to cancelled exactly once."""
        sync_session.add(test_listing)
        await sync_session.commit()
        await sync_session.refresh(test_listing)

        booking = Booking(
            listing_id=test_listing.id,
            pms_booking_id="RES_ACTIVE_CANCEL",
            guest_name="Active Guest",
            check_in_date=_test_date(3),
            check_out_date=_test_date(6),
            status="confirmed",
        )
        sync_session.add(booking)
        await sync_session.commit()

        mock_provider.get_reservations = AsyncMock(
            return_value=[
                _make_reservation(
                    pms_booking_id="RES_ACTIVE_CANCEL",
                    check_in=_test_date(3),
                    check_out=_test_date(6),
                    status="cancelled",
                )
            ]
        )

        service = SyncService(sync_session)
        first = await service.sync_listing(test_listing, mock_provider)
        assert first["cancelled"] == 1

        await sync_session.refresh(booking)
        cancelled_at = booking.updated_at

        second = await service.sync_listing(test_listing, mock_provider)
        assert second["cancelled"] == 0

        await sync_session.refresh(booking)
        assert booking.status == "cancelled"
        assert booking.updated_at == cancelled_at

    @pytest.mark.asyncio
    async def test_sync_skips_reservations_beyond_retention(
        self, sync_session, test_listing, mock_provider
    ):
        """Test stays checked out before the retention cutoff are not stored."""
        sync_session.add(test_listing)
        await sync_session.commit()
        await sync_session.refresh(test_listing)

        now = datetime.now(UTC)
        old_check_out = now - timedelta(days=OLD_BOOKING_RETENTION_DAYS + 5)
        mock_provider.get_reservations = AsyncMock(
            return_value=[
                _make_reservation(
                    pms_booking_id="RES_PURGED",
                    check_in=old_check_out - timedelta(days=3),
                    check_out=old_check_out,
                    status="checked_out",
                )
            ]
        )

        service = SyncService(sync_session)
        result = await service.sync_listing(test_listing, mock_provider)

        assert result["inserted"] == 0
        stored = await sync_session.execute(
            select(Booking).where(Booking.pms_booking_id == "RES_PURGED")
        )
        assert stored.scalar_one_or_none() is None
        # Enrichment must be skipped for discarded reservations
        mock_provider.get_guest.assert_not_called()

    @pytest.mark.asyncio
    async def test_sync_keeps_reservations_inside_retention(
        self, sync_session, test_listing, mock_provider
    ):
        """Test stays checked out inside the retention cutoff are stored."""
        sync_session.add(test_listing)
        await sync_session.commit()
        await sync_session.refresh(test_listing)

        now = datetime.now(UTC)
        recent_check_out = now - timedelta(days=OLD_BOOKING_RETENTION_DAYS - 5)
        mock_provider.get_reservations = AsyncMock(
            return_value=[
                _make_reservation(
                    pms_booking_id="RES_RECENT",
                    check_in=recent_check_out - timedelta(days=3),
                    check_out=recent_check_out,
                    status="checked_out",
                )
            ]
        )

        service = SyncService(sync_session)
        result = await service.sync_listing(test_listing, mock_provider)

        assert result["inserted"] == 1
        stored = await sync_session.execute(
            select(Booking).where(Booking.pms_booking_id == "RES_RECENT")
        )
        assert stored.scalar_one() is not None

    @pytest.mark.asyncio
    async def test_sync_keeps_long_in_progress_stay(
        self, sync_session, test_listing, mock_provider
    ):
        """Test a long stay started before the cutoff is still synced."""
        sync_session.add(test_listing)
        await sync_session.commit()
        await sync_session.refresh(test_listing)

        now = datetime.now(UTC)
        mock_provider.get_reservations = AsyncMock(
            return_value=[
                _make_reservation(
                    pms_booking_id="RES_LONG_STAY",
                    check_in=now - timedelta(days=200),
                    check_out=now + timedelta(days=5),
                    status="checked_in",
                )
            ]
        )

        service = SyncService(sync_session)
        result = await service.sync_listing(test_listing, mock_provider)

        assert result["inserted"] == 1

    @pytest.mark.asyncio
    async def test_sync_leaves_purgeable_booking_untouched(
        self, sync_session, test_listing, mock_provider
    ):
        """Test stored bookings past retention are not marked cancelled."""
        sync_session.add(test_listing)
        await sync_session.commit()
        await sync_session.refresh(test_listing)

        now = datetime.now(UTC)
        booking = Booking(
            listing_id=test_listing.id,
            pms_booking_id="RES_ANCIENT",
            guest_name="Ancient Guest",
            check_in_date=now - timedelta(days=OLD_BOOKING_RETENTION_DAYS + 10),
            check_out_date=now - timedelta(days=OLD_BOOKING_RETENTION_DAYS + 5),
            status="checked_out",
        )
        sync_session.add(booking)
        await sync_session.commit()

        mock_provider.get_reservations = AsyncMock(return_value=[])

        service = SyncService(sync_session)
        result = await service.sync_listing(test_listing, mock_provider)

        assert result["cancelled"] == 0
        await sync_session.refresh(booking)
        assert booking.status == "checked_out"

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


class TestIsInProgress:
    """Tests for the timezone-aware in-progress guard."""

    @staticmethod
    def _booking(check_in: datetime, check_out: datetime) -> Booking:
        """Build an unsaved booking with the given stay dates."""
        return Booking(
            listing_id=1,
            pms_booking_id="RES_TZ",
            guest_name="TZ Guest",
            check_in_date=check_in,
            check_out_date=check_out,
            status="confirmed",
        )

    @staticmethod
    def _listing(timezone: str) -> Listing:
        """Build an unsaved listing in the given timezone."""
        return Listing(
            pms_id="PROP_TZ",
            name="TZ Property",
            ical_url_slug="tz-property",
            enabled=True,
            sync_enabled=True,
            timezone=timezone,
        )

    def test_all_day_stay_in_progress_at_local_checkout_boundary(self):
        """Test an all-day stay is active until the local checkout date."""
        # Date-only PMS values are stored as midnight UTC. For a UTC-7
        # listing it is still October 15 locally at 01:00 UTC on October 16.
        booking = self._booking(
            datetime(2026, 10, 13, tzinfo=UTC),
            datetime(2026, 10, 16, tzinfo=UTC),
        )
        now = datetime(2026, 10, 16, 1, 0, tzinfo=UTC)

        assert (
            SyncService._is_in_progress(
                booking, self._listing("America/Los_Angeles"), now
            )
            is True
        )

    def test_all_day_stay_not_in_progress_after_local_checkout(self):
        """Test an all-day stay ends once the local checkout date passes."""
        booking = self._booking(
            datetime(2026, 10, 13, tzinfo=UTC),
            datetime(2026, 10, 16, tzinfo=UTC),
        )
        # 2026-10-16 18:00 UTC is 11:00 local on the checkout date.
        now = datetime(2026, 10, 16, 18, 0, tzinfo=UTC)

        assert (
            SyncService._is_in_progress(
                booking, self._listing("America/Los_Angeles"), now
            )
            is False
        )

    def test_all_day_stay_not_in_progress_before_local_check_in(self):
        """Test an all-day stay is not active before its check-in date."""
        booking = self._booking(
            datetime(2026, 10, 13, tzinfo=UTC),
            datetime(2026, 10, 16, tzinfo=UTC),
        )
        now = datetime(2026, 10, 12, 20, 0, tzinfo=UTC)

        assert (
            SyncService._is_in_progress(
                booking, self._listing("America/Los_Angeles"), now
            )
            is False
        )

    def test_timed_stay_compares_instants(self):
        """Test stays with real times are compared as instants."""
        booking = self._booking(
            datetime(2026, 10, 13, 16, 0, tzinfo=UTC),
            datetime(2026, 10, 16, 18, 0, tzinfo=UTC),
        )
        listing = self._listing("America/Los_Angeles")

        assert (
            SyncService._is_in_progress(
                booking, listing, datetime(2026, 10, 16, 1, 0, tzinfo=UTC)
            )
            is True
        )
        assert (
            SyncService._is_in_progress(
                booking, listing, datetime(2026, 10, 16, 18, 0, tzinfo=UTC)
            )
            is False
        )

    def test_invalid_timezone_falls_back_to_utc(self):
        """Test an unknown IANA name behaves like UTC."""
        booking = self._booking(
            datetime(2026, 10, 13, tzinfo=UTC),
            datetime(2026, 10, 16, tzinfo=UTC),
        )
        now = datetime(2026, 10, 15, 23, 0, tzinfo=UTC)

        listing = self._listing("Not/AZone")

        assert SyncService._is_in_progress(booking, listing, now) is True

    def test_missing_listing_defaults_to_utc(self):
        """Test the guard still works without a listing."""
        booking = self._booking(
            datetime(2026, 10, 13, tzinfo=UTC),
            datetime(2026, 10, 16, tzinfo=UTC),
        )

        assert (
            SyncService._is_in_progress(
                booking, None, datetime(2026, 10, 15, 23, 0, tzinfo=UTC)
            )
            is True
        )

    def test_naive_stored_values_treated_as_utc(self):
        """Test naive SQLite values are interpreted as UTC."""
        booking = self._booking(
            datetime(2026, 10, 13),
            datetime(2026, 10, 16),
        )
        now = datetime(2026, 10, 16, 1, 0, tzinfo=UTC)

        assert (
            SyncService._is_in_progress(
                booking, self._listing("America/Los_Angeles"), now
            )
            is True
        )

    @pytest.mark.asyncio
    async def test_sync_keeps_all_day_stay_ending_today(
        self, sync_session, mock_provider
    ):
        """Test the prune loop keeps a locally in-progress all-day stay."""
        listing = self._listing("America/Los_Angeles")
        sync_session.add(listing)
        await sync_session.commit()
        await sync_session.refresh(listing)

        now = datetime.now(UTC)
        today_utc_midnight = datetime(
            now.year, now.month, now.day, tzinfo=UTC
        ) + timedelta(days=1)
        booking = Booking(
            listing_id=listing.id,
            pms_booking_id="RES_LOCAL_BOUNDARY",
            guest_name="Boundary Guest",
            check_in_date=today_utc_midnight - timedelta(days=3),
            check_out_date=today_utc_midnight,
            status="confirmed",
        )
        sync_session.add(booking)
        await sync_session.commit()

        mock_provider.get_reservations = AsyncMock(return_value=[])

        service = SyncService(sync_session)
        result = await service.sync_listing(listing, mock_provider)

        assert result["cancelled"] == 0
        await sync_session.refresh(booking)
        assert booking.status == "confirmed"


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

    def test_extract_status_unknown_kept_verbatim(self, service, caplog):
        """Test unknown status is stored verbatim and warned about."""
        reservation = _make_reservation(status="  CANCELED_BY_CHANNEL ")
        with caplog.at_level(logging.WARNING, logger="src.services.sync_service"):
            result = service._extract_booking_data_from_dto(reservation)

        assert result["status"] == "canceled_by_channel"
        assert "'canceled_by_channel'" in caplog.text
        assert "RES001" in caplog.text

    @pytest.mark.parametrize("raw_status", ["", None])
    def test_extract_status_missing_not_confirmed(self, service, raw_status, caplog):
        """Test an empty/missing status is withheld, never confirmed."""
        reservation = _make_reservation(status=raw_status)
        with caplog.at_level(logging.WARNING, logger="src.services.sync_service"):
            result = service._extract_booking_data_from_dto(reservation)

        assert result["status"] == ""
        assert "booking withheld from calendar export" in caplog.text

    @pytest.mark.parametrize(
        "raw_status",
        ["confirmed", "checked_in", "checked_out", "cancelled"],
    )
    def test_extract_known_statuses_unchanged(self, service, raw_status, caplog):
        """Test recognised statuses pass through without a warning."""
        reservation = _make_reservation(status=raw_status)
        with caplog.at_level(logging.WARNING, logger="src.services.sync_service"):
            result = service._extract_booking_data_from_dto(reservation)

        assert result["status"] == raw_status
        assert "withheld" not in caplog.text

    @pytest.mark.parametrize(
        "raw_status",
        ["expired", "closed", "declined", "awaiting_payment"],
    )
    def test_extract_non_export_status_logs_debug(self, service, raw_status, caplog):
        """Test expected non-stay statuses are withheld without warning."""
        reservation = _make_reservation(status=raw_status)
        with caplog.at_level(logging.DEBUG, logger="src.services.sync_service"):
            result = service._extract_booking_data_from_dto(reservation)

        assert result["status"] == raw_status
        assert raw_status in caplog.text
        assert not [
            record for record in caplog.records if record.levelno >= logging.WARNING
        ]
        assert [
            record
            for record in caplog.records
            if record.levelno == logging.DEBUG and raw_status in record.getMessage()
        ]

    def test_extract_genuinely_unknown_status_warns(self, service, caplog):
        """Test an unknown status still warns about provider drift."""
        reservation = _make_reservation(status="zzz_unknown")
        with caplog.at_level(logging.DEBUG, logger="src.services.sync_service"):
            result = service._extract_booking_data_from_dto(reservation)

        assert result["status"] == "zzz_unknown"
        warnings = [
            record for record in caplog.records if record.levelno == logging.WARNING
        ]
        assert warnings
        assert "'zzz_unknown'" in warnings[0].getMessage()

    def test_extract_status_overlong_truncated(self, service):
        """Test an absurd status cannot overflow the String(50) column."""
        reservation = _make_reservation(status="x" * 200)
        result = service._extract_booking_data_from_dto(reservation)

        assert result["status"] == "x" * 50

    def test_room_ids_from_dto(self, service):
        """Test room IDs come from DTO tuple."""
        reservation = _make_reservation(
            room_ids=("ROOM_A", "ROOM_B"),
        )
        result = service._extract_booking_data_from_dto(reservation)

        assert result["room_ids"] == ["ROOM_A", "ROOM_B"]


class TestUnrecognisedStatusWithheld:
    """Tests for the withhold-do-not-retire policy (issue #121)."""

    @pytest.mark.asyncio
    async def test_unknown_status_stored_and_withheld(
        self, sync_session, mock_provider, caplog
    ):
        """Test unknown status is stored, kept, and not exported."""
        listing = Listing(
            pms_id="PROP123",
            name="Test Listing",
            ical_url_slug="test-listing",
            enabled=True,
            sync_enabled=True,
        )
        sync_session.add(listing)
        await sync_session.commit()
        await sync_session.refresh(listing)

        reservation = _make_reservation(status="canceled_by_channel")
        mock_provider.get_reservations = AsyncMock(return_value=[reservation])
        service = SyncService(sync_session)

        with caplog.at_level(logging.WARNING, logger="src.services.sync_service"):
            result = await service.sync_listing(listing, mock_provider)

        assert result["cancelled"] == 0
        assert "'canceled_by_channel'" in caplog.text

        repo = BookingRepository(sync_session)
        stored = await repo.get_by_pms_id(listing.id, "RES001")
        assert stored is not None
        assert stored.status == "canceled_by_channel"

        exported = await repo.get_confirmed_for_listing(listing.id)
        assert exported == []

        # Second sync: still present in the provider snapshot, so the prune
        # loop must leave it alone rather than retiring it.
        await service.sync_listing(listing, mock_provider)
        await sync_session.refresh(stored)
        assert stored.status == "canceled_by_channel"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "raw_status",
        ["expired", "closed", "declined", "awaiting_payment"],
    )
    async def test_non_export_status_withheld_not_retired(
        self, sync_session, mock_provider, caplog, raw_status
    ):
        """Test expected non-stay statuses are kept, quiet, and unexported."""
        listing = Listing(
            pms_id="PROP123",
            name="Test Listing",
            ical_url_slug="test-listing",
            enabled=True,
            sync_enabled=True,
        )
        sync_session.add(listing)
        await sync_session.commit()
        await sync_session.refresh(listing)

        reservation = _make_reservation(status=raw_status)
        mock_provider.get_reservations = AsyncMock(return_value=[reservation])
        service = SyncService(sync_session)

        with caplog.at_level(logging.DEBUG, logger="src.services.sync_service"):
            result = await service.sync_listing(listing, mock_provider)

        assert result["cancelled"] == 0
        assert not [
            record
            for record in caplog.records
            if record.levelno >= logging.WARNING and raw_status in record.getMessage()
        ]

        repo = BookingRepository(sync_session)
        stored = await repo.get_by_pms_id(listing.id, "RES001")
        assert stored is not None
        assert stored.status == raw_status

        exported = await repo.get_confirmed_for_listing(listing.id)
        assert exported == []

        # Second sync: still in the snapshot, so the prune loop must not
        # retire it as cancelled.
        await service.sync_listing(listing, mock_provider)
        await sync_session.refresh(stored)
        assert stored.status == raw_status


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
            check_in_date=_test_date(91),
            check_out_date=_test_date(95),
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
            check_in_date=_test_date(121),
            check_out_date=_test_date(125),
            status="confirmed",
        )
        booking2 = Booking(
            listing_id=listing2.id,
            pms_booking_id="RES_Y001",
            guest_name="Guest Y",
            check_in_date=_test_date(130),
            check_out_date=_test_date(135),
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

    @pytest.mark.asyncio
    async def test_sync_commits_status_without_caller_commit(
        self, sync_session, sync_session_factory, mock_provider
    ):
        """Sync status is durable without the caller committing afterwards."""
        listing = Listing(
            pms_id="SYNC_COMMIT",
            name="Sync Commit Test",
            ical_url_slug="sync-commit",
            enabled=True,
            sync_enabled=True,
            last_sync_at=None,
            last_sync_error="Previous error",
        )
        sync_session.add(listing)
        await sync_session.commit()
        listing_id = listing.id

        mock_provider.get_reservations = AsyncMock(return_value=[])

        service = SyncService(sync_session, session_factory=sync_session_factory)
        await service.sync_listing(listing, mock_provider)

        async with sync_session_factory() as reader:
            result = await reader.execute(
                select(Listing).where(Listing.id == listing_id)
            )
            persisted = result.scalar_one()
            assert persisted.last_sync_at is not None
            assert persisted.last_sync_error is None


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
            check_in_date=_test_date(1),
            check_out_date=_test_date(5),
            status="confirmed",
        )
        sync_session.add(existing_booking)
        await sync_session.commit()

        mock_provider.get_reservations = AsyncMock(
            return_value=[
                _make_reservation(
                    pms_booking_id="NEW_001",
                    guest_name="New Guest",
                    check_in=_test_date(31),
                    check_out=_test_date(35),
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
                    check_in=_test_date(31),
                    check_out=_test_date(35),
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
            check_in_date=_test_date(61),
            check_out_date=_test_date(65),
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
                    check_in=_test_date(91),
                    check_out=_test_date(95),
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
                    check_in=_test_date(121),
                    check_out=_test_date(125),
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
                    check_in=_test_date(121),
                    check_out=_test_date(125),
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

    @pytest.mark.asyncio
    async def test_unassigned_unit_logged_without_warning(
        self, sync_session, mock_provider, caplog
    ):
        """Test an unassigned multi-unit stay does not warn every sync.

        A reservation the PMS has not allocated to a physical unit reports
        the parent listing in the room position.  That is a normal state, so
        it must be reported informationally while still reaching the
        listing-level calendar.
        """
        from src.models.room import Room

        listing = Listing(
            pms_id="MTL_PARENT",
            name="Multi Unit",
            ical_url_slug="multi-unit",
            enabled=True,
            sync_enabled=True,
        )
        sync_session.add(listing)
        await sync_session.commit()
        await sync_session.refresh(listing)

        sync_session.add(
            Room(
                listing_id=listing.id,
                pms_room_id="MTL_CHILD",
                room_name="4785b",
                ical_url_slug="4785b",
                enabled=True,
            )
        )
        await sync_session.commit()

        mock_provider.get_reservations = AsyncMock(
            return_value=[
                _make_reservation(
                    pms_booking_id="RES_UNASSIGNED",
                    guest_name="Unassigned Guest",
                    room_ids=("MTL_PARENT",),
                ),
            ]
        )

        service = SyncService(sync_session)
        with caplog.at_level(logging.INFO, logger="src.services.sync_service"):
            await service.sync_listing(listing, mock_provider)

        assert "not assigned to a specific unit" in _sync_log_text(caplog)
        assert not [
            record
            for record in caplog.records
            if record.name == "src.services.sync_service"
            and record.levelno >= logging.WARNING
        ]

        stmt = select(Booking).where(
            Booking.pms_booking_id == "RES_UNASSIGNED::MTL_PARENT"
        )
        db_result = await sync_session.execute(stmt)
        assert db_result.scalar_one().room_id is None

    @pytest.mark.asyncio
    async def test_unknown_room_still_warns(self, sync_session, mock_provider, caplog):
        """Test a genuinely unknown room reference keeps warning."""
        from src.models.room import Room

        listing = Listing(
            pms_id="WARN_PARENT",
            name="Warn Listing",
            ical_url_slug="warn-listing",
            enabled=True,
            sync_enabled=True,
        )
        sync_session.add(listing)
        await sync_session.commit()
        await sync_session.refresh(listing)

        sync_session.add(
            Room(
                listing_id=listing.id,
                pms_room_id="KNOWN_ROOM",
                room_name="Known",
                ical_url_slug="known",
                enabled=True,
            )
        )
        await sync_session.commit()

        mock_provider.get_reservations = AsyncMock(
            return_value=[
                _make_reservation(
                    pms_booking_id="RES_STRAY",
                    guest_name="Stray Guest",
                    room_ids=("STRAY_ROOM",),
                ),
            ]
        )

        service = SyncService(sync_session)
        with caplog.at_level(logging.INFO, logger="src.services.sync_service"):
            await service.sync_listing(listing, mock_provider)

        assert "Room STRAY_ROOM not found" in _sync_log_text(caplog)

    @pytest.mark.asyncio
    async def test_unsynced_rooms_still_warn(self, sync_session, mock_provider, caplog):
        """Test a listing with no rooms yet keeps the actionable warning."""
        listing = Listing(
            pms_id="NO_ROOMS",
            name="No Rooms",
            ical_url_slug="no-rooms",
            enabled=True,
            sync_enabled=True,
        )
        sync_session.add(listing)
        await sync_session.commit()
        await sync_session.refresh(listing)

        mock_provider.get_reservations = AsyncMock(
            return_value=[
                _make_reservation(
                    pms_booking_id="RES_NO_ROOMS",
                    guest_name="Pending Guest",
                    room_ids=("NO_ROOMS",),
                ),
            ]
        )

        service = SyncService(sync_session)
        with caplog.at_level(logging.INFO, logger="src.services.sync_service"):
            await service.sync_listing(listing, mock_provider)

        assert "Sync rooms first" in _sync_log_text(caplog)


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
                    check_in=_test_date(61),
                    check_out=_test_date(65),
                ),
                _make_reservation(
                    pms_booking_id="RES_B",
                    guest_name=None,
                    guest_id="G100",  # Same guest
                    check_in=_test_date(91),
                    check_out=_test_date(95),
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
    async def test_guest_email_added_to_custom_data(
        self, sync_session, test_listing, mock_provider
    ):
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
        resolved, _ = await service._enrich_reservations(
            mock_provider, test_listing, reservations
        )

        r = resolved[0]
        assert r.custom_data["guest_email"] == "guest@example.com"
        assert r.custom_data["guest_phone"] == "+15559990000"
        assert r.custom_data["guest_phone_last4"] == "0000"

    @pytest.mark.asyncio
    async def test_enrichment_with_existing_name(
        self, sync_session, test_listing, mock_provider
    ):
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
        resolved, _ = await service._enrich_reservations(
            mock_provider, test_listing, reservations
        )

        r = resolved[0]
        # Original name kept
        assert r.guest_name == "Alice"
        # Phone and email enriched
        assert r.custom_data["guest_phone"] == "+15551234567"
        assert r.custom_data["guest_email"] == "alice@example.com"
        assert r.custom_data["guest_phone_last4"] == "4567"
        mock_provider.get_guest.assert_called_once_with("G1")

    @pytest.mark.asyncio
    async def test_no_guest_id_skips_enrichment(
        self, sync_session, test_listing, mock_provider
    ):
        """Reservation without guest_id is returned unchanged."""
        service = SyncService(sync_session)
        reservations = [
            _make_reservation(
                pms_booking_id="RES_NOID",
                guest_name="Bob",
                guest_id=None,
            ),
        ]
        resolved, _ = await service._enrich_reservations(
            mock_provider, test_listing, reservations
        )

        r = resolved[0]
        assert r.guest_name == "Bob"
        assert "guest_phone" not in r.custom_data
        assert "guest_email" not in r.custom_data
        mock_provider.get_guest.assert_not_called()

    @pytest.mark.asyncio
    async def test_already_enriched_skips_get_guest(
        self, sync_session, test_listing, mock_provider
    ):
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
        resolved, _ = await service._enrich_reservations(
            mock_provider, test_listing, reservations
        )

        r = resolved[0]
        assert r.guest_name == "Carol"
        assert r.custom_data["guest_phone"] == "+15550001111"
        assert r.custom_data["guest_email"] == "carol@example.com"
        mock_provider.get_guest.assert_not_called()

    @pytest.mark.asyncio
    async def test_enrichment_preserves_existing_custom_data(
        self, sync_session, test_listing, mock_provider
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
        resolved, _ = await service._enrich_reservations(
            mock_provider, test_listing, reservations
        )

        r = resolved[0]
        # Existing phone kept, API email filled in
        assert r.custom_data["guest_phone"] == "+15550001111"
        assert r.custom_data["guest_email"] == "api@example.com"


class TestCustomFieldEnrichment:
    """Tests for custom field enrichment step."""

    @pytest.mark.asyncio
    async def test_enrichment_merges_custom_fields(
        self, sync_session, test_listing, mock_provider
    ):
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
        enriched, _ = await service._enrich_reservations(
            mock_provider, test_listing, reservations
        )

        assert len(enriched) == 1
        assert enriched[0].custom_data["cf_1"] == "val1"
        assert enriched[0].custom_data["cf_2"] == "val2"
        # Existing custom_data wins on key conflicts
        assert enriched[0].custom_data["source"] == "airbnb"

    @pytest.mark.asyncio
    async def test_enrichment_noop_when_empty(
        self, sync_session, test_listing, mock_provider
    ):
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
        enriched, _ = await service._enrich_reservations(
            mock_provider, test_listing, reservations
        )

        assert enriched[0].custom_data == {"existing": "data"}

    @pytest.mark.asyncio
    async def test_enrichment_handles_provider_error(
        self, sync_session, test_listing, mock_provider
    ):
        """Test enrichment handles provider errors gracefully."""
        mock_provider.has_separate_custom_fields = True
        mock_provider.get_custom_fields = AsyncMock(
            side_effect=PMSProviderError("API error")
        )

        service = SyncService(sync_session)
        reservations = [
            _make_reservation(pms_booking_id="RES_ERR"),
        ]
        enriched, _ = await service._enrich_reservations(
            mock_provider, test_listing, reservations
        )

        assert len(enriched) == 1
        assert enriched[0].pms_booking_id == "RES_ERR"

    @pytest.mark.asyncio
    async def test_enrichment_skipped_when_not_supported(
        self, sync_session, test_listing, mock_provider
    ):
        """Test enrichment is skipped for providers without separate fields."""
        mock_provider.has_separate_custom_fields = False

        service = SyncService(sync_session)
        reservations = [
            _make_reservation(pms_booking_id="RES_SKIP"),
        ]
        enriched, _ = await service._enrich_reservations(
            mock_provider, test_listing, reservations
        )

        assert enriched == reservations
        mock_provider.get_custom_fields.assert_not_called()


class TestCustomFieldEnrichmentChangeDetection:
    """Tests for skipping redundant custom-field fetches (issue #128)."""

    @staticmethod
    async def _persisted_listing(sync_session, test_listing):
        """Persist and return the shared test listing."""
        sync_session.add(test_listing)
        await sync_session.commit()
        await sync_session.refresh(test_listing)
        return test_listing

    @staticmethod
    def _custom_field_provider(mock_provider):
        """Configure the mock provider to expose separate custom fields."""
        mock_provider.has_separate_custom_fields = True
        mock_provider.provider_type = "guesty"
        mock_provider.get_custom_fields = AsyncMock(
            return_value={"door_code": "1234"},
        )
        return mock_provider

    @pytest.mark.asyncio
    async def test_unchanged_past_reservation_not_refetched(
        self, sync_session, test_listing, mock_provider
    ):
        """Test an unchanged, long-past stay is enriched only once."""
        listing = await self._persisted_listing(sync_session, test_listing)
        self._custom_field_provider(mock_provider)

        reservation = _make_reservation(
            pms_booking_id="RES_PAST",
            check_in=_test_date(-40),
            check_out=_test_date(-35),
            updated_at=_test_date(-40),
        )
        mock_provider.get_reservations = AsyncMock(return_value=[reservation])

        service = SyncService(sync_session)
        await service.sync_listing(listing, mock_provider)
        assert mock_provider.get_custom_fields.await_count == 1

        await service.sync_listing(listing, mock_provider)

        assert mock_provider.get_custom_fields.await_count == 1
        booking = await BookingRepository(sync_session).get_by_pms_id(
            listing.id, "RES_PAST"
        )
        assert booking is not None
        assert booking.custom_data["door_code"] == "1234"

    @pytest.mark.asyncio
    async def test_near_future_reservation_always_refetched(
        self, sync_session, test_listing, mock_provider
    ):
        """Test an imminent stay is re-enriched even when unchanged.

        Door codes depend on this data, so change detection is never
        trusted inside the freshness window.
        """
        listing = await self._persisted_listing(sync_session, test_listing)
        self._custom_field_provider(mock_provider)

        reservation = _make_reservation(
            pms_booking_id="RES_SOON",
            check_in=_test_date(2),
            check_out=_test_date(5),
            updated_at=_test_date(-10),
        )
        mock_provider.get_reservations = AsyncMock(return_value=[reservation])

        service = SyncService(sync_session)
        await service.sync_listing(listing, mock_provider)
        await service.sync_listing(listing, mock_provider)

        assert mock_provider.get_custom_fields.await_count == 2

    @pytest.mark.asyncio
    async def test_in_progress_reservation_always_refetched(
        self, sync_session, test_listing, mock_provider
    ):
        """Test a stay underway is re-enriched even when unchanged."""
        listing = await self._persisted_listing(sync_session, test_listing)
        self._custom_field_provider(mock_provider)

        reservation = _make_reservation(
            pms_booking_id="RES_NOW",
            check_in=_test_date(-1),
            check_out=_test_date(2),
            updated_at=_test_date(-20),
        )
        mock_provider.get_reservations = AsyncMock(return_value=[reservation])

        service = SyncService(sync_session)
        await service.sync_listing(listing, mock_provider)
        await service.sync_listing(listing, mock_provider)

        assert mock_provider.get_custom_fields.await_count == 2

    @pytest.mark.asyncio
    async def test_changed_reservation_is_refetched(
        self, sync_session, test_listing, mock_provider
    ):
        """Test a reservation with a newer timestamp is re-enriched."""
        listing = await self._persisted_listing(sync_session, test_listing)
        self._custom_field_provider(mock_provider)

        mock_provider.get_reservations = AsyncMock(
            return_value=[
                _make_reservation(
                    pms_booking_id="RES_CHANGED",
                    check_in=_test_date(-40),
                    check_out=_test_date(-35),
                    updated_at=_test_date(-40),
                )
            ]
        )

        service = SyncService(sync_session)
        await service.sync_listing(listing, mock_provider)

        mock_provider.get_custom_fields = AsyncMock(
            return_value={"door_code": "9999"},
        )
        mock_provider.get_reservations = AsyncMock(
            return_value=[
                _make_reservation(
                    pms_booking_id="RES_CHANGED",
                    check_in=_test_date(-40),
                    check_out=_test_date(-35),
                    updated_at=_test_date(-1),
                )
            ]
        )
        await service.sync_listing(listing, mock_provider)

        assert mock_provider.get_custom_fields.await_count == 1
        booking = await BookingRepository(sync_session).get_by_pms_id(
            listing.id, "RES_CHANGED"
        )
        assert booking is not None
        assert booking.custom_data["door_code"] == "9999"

    @pytest.mark.asyncio
    async def test_missing_timestamp_disables_skipping(
        self, sync_session, test_listing, mock_provider
    ):
        """Test providers without a change signal keep fetching every sync."""
        listing = await self._persisted_listing(sync_session, test_listing)
        self._custom_field_provider(mock_provider)

        mock_provider.get_reservations = AsyncMock(
            return_value=[
                _make_reservation(
                    pms_booking_id="RES_NO_TS",
                    check_in=_test_date(-40),
                    check_out=_test_date(-35),
                )
            ]
        )

        service = SyncService(sync_session)
        await service.sync_listing(listing, mock_provider)
        await service.sync_listing(listing, mock_provider)

        assert mock_provider.get_custom_fields.await_count == 2

    @pytest.mark.asyncio
    async def test_enrichment_failure_keeps_booking_and_last_good_data(
        self, sync_session, test_listing, mock_provider
    ):
        """Test an enrichment failure neither prunes nor blanks a booking."""
        listing = await self._persisted_listing(sync_session, test_listing)
        self._custom_field_provider(mock_provider)

        mock_provider.get_reservations = AsyncMock(
            return_value=[
                _make_reservation(
                    pms_booking_id="RES_FAIL",
                    check_in=_test_date(1),
                    check_out=_test_date(4),
                    updated_at=_test_date(-2),
                )
            ]
        )

        service = SyncService(sync_session)
        await service.sync_listing(listing, mock_provider)

        mock_provider.get_custom_fields = AsyncMock(
            side_effect=PMSProviderError("rate limited"),
        )
        counts = await service.sync_listing(listing, mock_provider)

        assert counts["cancelled"] == 0
        booking = await BookingRepository(sync_session).get_by_pms_id(
            listing.id, "RES_FAIL"
        )
        assert booking is not None
        assert booking.status == "confirmed"
        assert booking.custom_data["door_code"] == "1234"

    @pytest.mark.asyncio
    async def test_updated_at_survives_guest_resolution(
        self, sync_session, test_listing, mock_provider
    ):
        """Test the change signal survives the whole enrichment pipeline.

        Guest resolution rebuilds the DTO, and a dropped ``updated_at``
        there silently disables reuse for every reservation with a guest.
        """
        self._custom_field_provider(mock_provider)
        mock_provider.get_guest = AsyncMock(
            return_value=PMSGuest(
                guest_id="G_TS",
                full_name="Pipeline Guest",
                phone="+15550001234",
                email="pipeline@example.com",
            )
        )
        stamp = _test_date(-40)
        reservations = [
            _make_reservation(
                pms_booking_id="RES_PIPE",
                guest_id="G_TS",
                check_in=_test_date(-40),
                check_out=_test_date(-35),
                updated_at=stamp,
            )
        ]

        service = SyncService(sync_session)
        enriched, _ = await service._enrich_reservations(
            mock_provider, test_listing, reservations
        )

        assert enriched[0].updated_at == stamp
        assert enriched[0].custom_data["guest_phone"] == "+15550001234"
        assert enriched[0].custom_data["door_code"] == "1234"

    @pytest.mark.asyncio
    async def test_guest_lookup_skipped_when_unchanged(
        self, sync_session, test_listing, mock_provider
    ):
        """Test an unchanged out-of-window stay skips both enrichments."""
        listing = await self._persisted_listing(sync_session, test_listing)
        self._custom_field_provider(mock_provider)
        mock_provider.get_guest = AsyncMock(
            return_value=PMSGuest(
                guest_id="G_SKIP",
                full_name="Past Guest",
                phone="+15550009876",
                email="past@example.com",
            )
        )
        mock_provider.get_reservations = AsyncMock(
            return_value=[
                _make_reservation(
                    pms_booking_id="RES_GUEST_PAST",
                    guest_id="G_SKIP",
                    check_in=_test_date(-40),
                    check_out=_test_date(-35),
                    updated_at=_test_date(-40),
                )
            ]
        )

        service = SyncService(sync_session)
        await service.sync_listing(listing, mock_provider)
        assert mock_provider.get_guest.await_count == 1
        assert mock_provider.get_custom_fields.await_count == 1

        await service.sync_listing(listing, mock_provider)

        assert mock_provider.get_guest.await_count == 1
        assert mock_provider.get_custom_fields.await_count == 1
        booking = await BookingRepository(sync_session).get_by_pms_id(
            listing.id, "RES_GUEST_PAST"
        )
        assert booking is not None
        assert booking.guest_phone_last4 == "9876"
        assert booking.custom_data["guest_email"] == "past@example.com"

    @pytest.mark.asyncio
    async def test_guest_lookup_always_runs_in_window(
        self, sync_session, test_listing, mock_provider
    ):
        """Test an imminent stay re-fetches guest data every sync.

        Rental Control turns the guest phone last-4 into a lock code, so
        this is the same door-code constraint as the custom fields.
        """
        listing = await self._persisted_listing(sync_session, test_listing)
        self._custom_field_provider(mock_provider)
        mock_provider.get_guest = AsyncMock(
            return_value=PMSGuest(
                guest_id="G_SOON",
                full_name="Soon Guest",
                phone="+15550005555",
                email="soon@example.com",
            )
        )
        mock_provider.get_reservations = AsyncMock(
            return_value=[
                _make_reservation(
                    pms_booking_id="RES_GUEST_SOON",
                    guest_id="G_SOON",
                    check_in=_test_date(3),
                    check_out=_test_date(6),
                    updated_at=_test_date(-10),
                )
            ]
        )

        service = SyncService(sync_session)
        await service.sync_listing(listing, mock_provider)
        await service.sync_listing(listing, mock_provider)

        assert mock_provider.get_guest.await_count == 2
        assert mock_provider.get_custom_fields.await_count == 2

    @pytest.mark.asyncio
    async def test_failed_enrichment_retried_next_sync(
        self, sync_session, test_listing, mock_provider
    ):
        """Test a failed fetch does not suppress the next sync's retry."""
        listing = await self._persisted_listing(sync_session, test_listing)
        self._custom_field_provider(mock_provider)
        mock_provider.get_reservations = AsyncMock(
            return_value=[
                _make_reservation(
                    pms_booking_id="RES_RETRY",
                    check_in=_test_date(-40),
                    check_out=_test_date(-35),
                    updated_at=_test_date(-40),
                )
            ]
        )

        service = SyncService(sync_session)
        await service.sync_listing(listing, mock_provider)

        # Second sync: the reservation changed and the fetch fails.
        mock_provider.get_custom_fields = AsyncMock(
            side_effect=PMSProviderError("rate limited"),
        )
        mock_provider.get_reservations = AsyncMock(
            return_value=[
                _make_reservation(
                    pms_booking_id="RES_RETRY",
                    check_in=_test_date(-40),
                    check_out=_test_date(-35),
                    updated_at=_test_date(-2),
                )
            ]
        )
        await service.sync_listing(listing, mock_provider)

        booking = await BookingRepository(sync_session).get_by_pms_id(
            listing.id, "RES_RETRY"
        )
        assert booking is not None
        assert booking.pms_updated_at is None
        assert booking.custom_data["door_code"] == "1234"

        # Third sync: the fetch succeeds again and must have been retried.
        mock_provider.get_custom_fields = AsyncMock(
            return_value={"door_code": "4321"},
        )
        await service.sync_listing(listing, mock_provider)

        assert mock_provider.get_custom_fields.await_count == 1
        booking = await BookingRepository(sync_session).get_by_pms_id(
            listing.id, "RES_RETRY"
        )
        assert booking is not None
        assert booking.custom_data["door_code"] == "4321"

    @pytest.mark.asyncio
    async def test_first_sync_failure_is_not_cached(
        self, sync_session, test_listing, mock_provider
    ):
        """Test a first-sync failure is retried even with payload data.

        Reservation payload fields make ``custom_data`` nonempty, which
        must not be mistaken for a successful enrichment read.
        """
        listing = await self._persisted_listing(sync_session, test_listing)
        self._custom_field_provider(mock_provider)
        mock_provider.get_custom_fields = AsyncMock(
            side_effect=PMSProviderError("rate limited"),
        )
        mock_provider.get_reservations = AsyncMock(
            return_value=[
                _make_reservation(
                    pms_booking_id="RES_FIRST_FAIL",
                    check_in=_test_date(-40),
                    check_out=_test_date(-35),
                    updated_at=_test_date(-40),
                    custom_data={"source": "airbnb"},
                )
            ]
        )

        service = SyncService(sync_session)
        await service.sync_listing(listing, mock_provider)

        booking = await BookingRepository(sync_session).get_by_pms_id(
            listing.id, "RES_FIRST_FAIL"
        )
        assert booking is not None
        assert booking.custom_data["source"] == "airbnb"
        assert booking.pms_updated_at is None

        mock_provider.get_custom_fields = AsyncMock(
            return_value={"door_code": "1234"},
        )
        await service.sync_listing(listing, mock_provider)

        assert mock_provider.get_custom_fields.await_count == 1
        booking = await BookingRepository(sync_session).get_by_pms_id(
            listing.id, "RES_FIRST_FAIL"
        )
        assert booking is not None
        assert booking.custom_data["door_code"] == "1234"

    @pytest.mark.asyncio
    async def test_cleared_payload_field_not_resurrected(
        self, sync_session, test_listing, mock_provider
    ):
        """Test a failed fetch does not restore a deleted reservation field.

        Only enrichment-owned values may be reused: clearing a note in the
        PMS must stay cleared even when enrichment then fails.
        """
        listing = await self._persisted_listing(sync_session, test_listing)
        self._custom_field_provider(mock_provider)
        mock_provider.get_reservations = AsyncMock(
            return_value=[
                _make_reservation(
                    pms_booking_id="RES_NOTES",
                    check_in=_test_date(-40),
                    check_out=_test_date(-35),
                    updated_at=_test_date(-40),
                    custom_data={"notes": "leave key under mat"},
                )
            ]
        )

        service = SyncService(sync_session)
        await service.sync_listing(listing, mock_provider)

        booking = await BookingRepository(sync_session).get_by_pms_id(
            listing.id, "RES_NOTES"
        )
        assert booking is not None
        assert booking.custom_data["notes"] == "leave key under mat"

        # The note is cleared in the PMS and enrichment then fails.
        mock_provider.get_custom_fields = AsyncMock(
            side_effect=PMSProviderError("rate limited"),
        )
        mock_provider.get_reservations = AsyncMock(
            return_value=[
                _make_reservation(
                    pms_booking_id="RES_NOTES",
                    check_in=_test_date(-40),
                    check_out=_test_date(-35),
                    updated_at=_test_date(-1),
                    custom_data={},
                )
            ]
        )
        await service.sync_listing(listing, mock_provider)

        booking = await BookingRepository(sync_session).get_by_pms_id(
            listing.id, "RES_NOTES"
        )
        assert booking is not None
        assert "notes" not in booking.custom_data
        # The enrichment-owned value is still kept.
        assert booking.custom_data["door_code"] == "1234"

    @pytest.mark.asyncio
    async def test_reassignment_fallback_uses_current_row(
        self, sync_session, test_listing, mock_provider
    ):
        """Test a retired room row never supplies the fallback values.

        A room reassignment leaves the previous row behind as cancelled;
        reusing its obsolete door code would publish a stale lock code.
        """
        from src.models.room import Room

        listing = await self._persisted_listing(sync_session, test_listing)
        sync_session.add_all(
            [
                Room(
                    listing_id=listing.id,
                    pms_room_id="RM-0",
                    room_name="Room A",
                    ical_url_slug="rm-a",
                    enabled=True,
                ),
                Room(
                    listing_id=listing.id,
                    pms_room_id="RM-1",
                    room_name="Room B",
                    ical_url_slug="rm-b",
                    enabled=True,
                ),
            ]
        )
        await sync_session.commit()
        self._custom_field_provider(mock_provider)

        def _payload(room, check_in_offset, stamp_offset):
            """Build a single-reservation provider response."""
            return [
                _make_reservation(
                    pms_booking_id="RES_MOVE",
                    check_in=_test_date(check_in_offset),
                    check_out=_test_date(check_in_offset + 5),
                    updated_at=_test_date(stamp_offset),
                    room_ids=(room,),
                )
            ]

        service = SyncService(sync_session)
        mock_provider.get_custom_fields = AsyncMock(return_value={"door_code": "1111"})
        mock_provider.get_reservations = AsyncMock(
            return_value=_payload("RM-0", -45, -45)
        )
        await service.sync_listing(listing, mock_provider)

        # Reassigned to a later room and a later date: the retired row now
        # sorts first by check-in date.
        mock_provider.get_custom_fields = AsyncMock(return_value={"door_code": "2222"})
        mock_provider.get_reservations = AsyncMock(
            return_value=_payload("RM-1", -43, -20)
        )
        await service.sync_listing(listing, mock_provider)

        # The reservation changes again and the fetch fails.
        mock_provider.get_custom_fields = AsyncMock(
            side_effect=PMSProviderError("rate limited"),
        )
        mock_provider.get_reservations = AsyncMock(
            return_value=_payload("RM-1", -43, -10)
        )
        await service.sync_listing(listing, mock_provider)

        booking = await BookingRepository(sync_session).get_by_pms_id(
            listing.id, "RES_MOVE::RM-1"
        )
        assert booking is not None
        assert booking.custom_data["door_code"] == "2222"

    @pytest.mark.asyncio
    async def test_successful_empty_response_is_reused(
        self, sync_session, test_listing, mock_provider
    ):
        """Test an empty but successful read still suppresses re-fetching.

        Accounts that configure no custom fields otherwise gain nothing.
        """
        listing = await self._persisted_listing(sync_session, test_listing)
        self._custom_field_provider(mock_provider)
        mock_provider.get_custom_fields = AsyncMock(return_value={})
        mock_provider.get_reservations = AsyncMock(
            return_value=[
                _make_reservation(
                    pms_booking_id="RES_EMPTY",
                    check_in=_test_date(-40),
                    check_out=_test_date(-35),
                    updated_at=_test_date(-40),
                )
            ]
        )

        service = SyncService(sync_session)
        await service.sync_listing(listing, mock_provider)
        assert mock_provider.get_custom_fields.await_count == 1

        await service.sync_listing(listing, mock_provider)

        assert mock_provider.get_custom_fields.await_count == 1

    @pytest.mark.asyncio
    async def test_unstored_enrichment_is_not_reused(
        self, sync_session, test_listing, mock_provider
    ):
        """Test a row predating the enrichment column is always re-fetched.

        A NULL column means "never stored", which must not be mistaken for
        a successful empty read: reusing it would drop a published code.
        """
        listing = await self._persisted_listing(sync_session, test_listing)
        self._custom_field_provider(mock_provider)
        mock_provider.get_reservations = AsyncMock(
            return_value=[
                _make_reservation(
                    pms_booking_id="RES_LEGACY",
                    check_in=_test_date(-40),
                    check_out=_test_date(-35),
                    updated_at=_test_date(-40),
                )
            ]
        )

        service = SyncService(sync_session)
        await service.sync_listing(listing, mock_provider)

        repo = BookingRepository(sync_session)
        booking = await repo.get_by_pms_id(listing.id, "RES_LEGACY")
        assert booking is not None
        booking.enrichment_data = None
        await sync_session.commit()

        await service.sync_listing(listing, mock_provider)

        assert mock_provider.get_custom_fields.await_count == 2

    @pytest.mark.asyncio
    async def test_failed_lookup_drops_other_guests_details(
        self, sync_session, test_listing, mock_provider
    ):
        """Test a reassigned guest never inherits the old guest's code.

        The stored phone last-4 becomes a physical lock code, so falling
        back to it after a reassignment would leave the previous guest
        with working access under the new guest's name.
        """
        listing = await self._persisted_listing(sync_session, test_listing)
        self._custom_field_provider(mock_provider)
        mock_provider.get_guest = AsyncMock(
            return_value=PMSGuest(
                guest_id="G_FIRST",
                full_name="First Guest",
                phone="+15550001111",
                email="first@example.com",
            )
        )
        mock_provider.get_reservations = AsyncMock(
            return_value=[
                _make_reservation(
                    pms_booking_id="RES_SWAP",
                    guest_id="G_FIRST",
                    guest_name="First Guest",
                    check_in=_test_date(3),
                    check_out=_test_date(6),
                    updated_at=_test_date(-5),
                )
            ]
        )

        service = SyncService(sync_session)
        await service.sync_listing(listing, mock_provider)

        repo = BookingRepository(sync_session)
        booking = await repo.get_by_pms_id(listing.id, "RES_SWAP")
        assert booking is not None
        assert booking.guest_phone_last4 == "1111"

        # The reservation is reassigned and the new lookup fails.
        mock_provider.get_guest = AsyncMock(
            side_effect=PMSProviderError("rate limited"),
        )
        mock_provider.get_reservations = AsyncMock(
            return_value=[
                _make_reservation(
                    pms_booking_id="RES_SWAP",
                    guest_id="G_SECOND",
                    guest_name="Second Guest",
                    check_in=_test_date(3),
                    check_out=_test_date(6),
                    updated_at=_test_date(-1),
                )
            ]
        )
        await service.sync_listing(listing, mock_provider)

        booking = await repo.get_by_pms_id(listing.id, "RES_SWAP")
        assert booking is not None
        assert booking.guest_name == "Second Guest"
        assert booking.guest_phone_last4 is None
        assert "guest_phone" not in booking.custom_data
        assert "guest_email" not in booking.custom_data
        assert booking.pms_updated_at is None

    @pytest.mark.asyncio
    async def test_same_guest_failure_keeps_details(
        self, sync_session, test_listing, mock_provider
    ):
        """Test an unchanged guest still keeps its details on failure."""
        listing = await self._persisted_listing(sync_session, test_listing)
        self._custom_field_provider(mock_provider)
        mock_provider.get_guest = AsyncMock(
            return_value=PMSGuest(
                guest_id="G_SAME",
                full_name="Same Guest",
                phone="+15550002222",
                email="same@example.com",
            )
        )
        mock_provider.get_reservations = AsyncMock(
            return_value=[
                _make_reservation(
                    pms_booking_id="RES_SAME",
                    guest_id="G_SAME",
                    guest_name="Same Guest",
                    check_in=_test_date(3),
                    check_out=_test_date(6),
                    updated_at=_test_date(-5),
                )
            ]
        )

        service = SyncService(sync_session)
        await service.sync_listing(listing, mock_provider)

        mock_provider.get_guest = AsyncMock(
            side_effect=PMSProviderError("rate limited"),
        )
        await service.sync_listing(listing, mock_provider)

        booking = await BookingRepository(sync_session).get_by_pms_id(
            listing.id, "RES_SAME"
        )
        assert booking is not None
        assert booking.guest_phone_last4 == "2222"
        assert booking.custom_data["guest_email"] == "same@example.com"

    @pytest.mark.asyncio
    async def test_guest_bookkeeping_key_not_exported(
        self, sync_session, test_listing, mock_provider
    ):
        """Test the stored guest provenance never reaches custom data."""
        listing = await self._persisted_listing(sync_session, test_listing)
        self._custom_field_provider(mock_provider)
        mock_provider.get_guest = AsyncMock(
            return_value=PMSGuest(
                guest_id="G_HIDDEN",
                full_name="Hidden Guest",
                phone="+15550003333",
                email="hidden@example.com",
            )
        )
        mock_provider.get_reservations = AsyncMock(
            return_value=[
                _make_reservation(
                    pms_booking_id="RES_HIDDEN",
                    guest_id="G_HIDDEN",
                    check_in=_test_date(3),
                    check_out=_test_date(6),
                    updated_at=_test_date(-5),
                )
            ]
        )

        service = SyncService(sync_session)
        await service.sync_listing(listing, mock_provider)

        enriched, payloads = await service._enrich_reservations(
            mock_provider,
            listing,
            [
                _make_reservation(
                    pms_booking_id="RES_HIDDEN2",
                    guest_id="G_HIDDEN",
                    check_in=_test_date(3),
                    check_out=_test_date(6),
                )
            ],
        )
        assert GUEST_SOURCE_ID_KEY not in enriched[0].custom_data
        assert payloads["RES_HIDDEN2"][GUEST_SOURCE_ID_KEY] == "G_HIDDEN"

        booking = await BookingRepository(sync_session).get_by_pms_id(
            listing.id, "RES_HIDDEN"
        )
        assert booking is not None
        assert GUEST_SOURCE_ID_KEY not in booking.custom_data
        assert booking.enrichment_data[GUEST_SOURCE_ID_KEY] == "G_HIDDEN"

    @pytest.mark.asyncio
    async def test_upgrade_failure_keeps_published_values(
        self, sync_session, test_listing, mock_provider
    ):
        """Test a failed first post-upgrade fetch keeps published codes.

        Rows written before the enrichment column carry NULL provenance;
        an empty fallback there would delete live door codes.
        """
        listing = await self._persisted_listing(sync_session, test_listing)
        self._custom_field_provider(mock_provider)
        mock_provider.get_reservations = AsyncMock(
            return_value=[
                _make_reservation(
                    pms_booking_id="RES_UPGRADE",
                    check_in=_test_date(3),
                    check_out=_test_date(6),
                    updated_at=_test_date(-5),
                )
            ]
        )

        service = SyncService(sync_session)
        await service.sync_listing(listing, mock_provider)

        repo = BookingRepository(sync_session)
        booking = await repo.get_by_pms_id(listing.id, "RES_UPGRADE")
        assert booking is not None
        # Simulate a row that predates migration a7b8c9d0e1f2.
        booking.enrichment_data = None
        booking.pms_updated_at = None
        await sync_session.commit()

        mock_provider.get_custom_fields = AsyncMock(
            side_effect=PMSProviderError("rate limited"),
        )
        await service.sync_listing(listing, mock_provider)

        booking = await repo.get_by_pms_id(listing.id, "RES_UPGRADE")
        assert booking is not None
        assert booking.custom_data["door_code"] == "1234"
        assert booking.pms_updated_at is None

    @pytest.mark.asyncio
    async def test_upgrade_failure_keeps_deletions(
        self, sync_session, test_listing, mock_provider
    ):
        """Test bootstrap recovery never revives a cleared payload field.

        The key stays visible on the listing's other reservations, which
        is what marks it as reservation-owned rather than endpoint-owned.
        """
        listing = await self._persisted_listing(sync_session, test_listing)
        self._custom_field_provider(mock_provider)
        other = _make_reservation(
            pms_booking_id="RES_UPGRADE_OTHER",
            check_in=_test_date(3),
            check_out=_test_date(6),
            updated_at=_test_date(-5),
            custom_data={"notes": "ring the bell"},
        )
        mock_provider.get_reservations = AsyncMock(
            return_value=[
                _make_reservation(
                    pms_booking_id="RES_UPGRADE_NOTES",
                    check_in=_test_date(3),
                    check_out=_test_date(6),
                    updated_at=_test_date(-5),
                    custom_data={"notes": "leave key under mat"},
                ),
                other,
            ]
        )

        service = SyncService(sync_session)
        await service.sync_listing(listing, mock_provider)

        repo = BookingRepository(sync_session)
        booking = await repo.get_by_pms_id(listing.id, "RES_UPGRADE_NOTES")
        assert booking is not None
        booking.enrichment_data = None
        booking.pms_updated_at = None
        await sync_session.commit()

        # The note is cleared and the first post-upgrade fetch fails.
        mock_provider.get_custom_fields = AsyncMock(
            side_effect=PMSProviderError("rate limited"),
        )
        mock_provider.get_reservations = AsyncMock(
            return_value=[
                _make_reservation(
                    pms_booking_id="RES_UPGRADE_NOTES",
                    check_in=_test_date(3),
                    check_out=_test_date(6),
                    updated_at=_test_date(-1),
                    custom_data={},
                ),
                other,
            ]
        )
        await service.sync_listing(listing, mock_provider)

        booking = await repo.get_by_pms_id(listing.id, "RES_UPGRADE_NOTES")
        assert booking is not None
        assert "notes" not in booking.custom_data
        assert booking.custom_data["door_code"] == "1234"

    @pytest.mark.asyncio
    async def test_upgrade_failure_drops_unattributed_guest(
        self, sync_session, test_listing, mock_provider
    ):
        """Test legacy guest values are not recovered on a failed lookup.

        A pre-upgrade row records no source guest ID, so the stored phone
        cannot be proven to belong to the current guest.  Publishing no
        lock code is safer than publishing someone else's.
        """
        listing = await self._persisted_listing(sync_session, test_listing)
        self._custom_field_provider(mock_provider)
        mock_provider.get_guest = AsyncMock(
            return_value=PMSGuest(
                guest_id="G_LEGACY",
                full_name="Legacy Guest",
                phone="+15550007777",
                email="legacy@example.com",
            )
        )
        mock_provider.get_reservations = AsyncMock(
            return_value=[
                _make_reservation(
                    pms_booking_id="RES_UPGRADE_GUEST",
                    guest_id="G_LEGACY",
                    guest_name="Legacy Guest",
                    check_in=_test_date(3),
                    check_out=_test_date(6),
                    updated_at=_test_date(-5),
                )
            ]
        )

        service = SyncService(sync_session)
        await service.sync_listing(listing, mock_provider)

        repo = BookingRepository(sync_session)
        booking = await repo.get_by_pms_id(listing.id, "RES_UPGRADE_GUEST")
        assert booking is not None
        assert booking.guest_phone_last4 == "7777"
        booking.enrichment_data = None
        booking.pms_updated_at = None
        await sync_session.commit()

        mock_provider.get_guest = AsyncMock(
            side_effect=PMSProviderError("rate limited"),
        )
        # The custom-field fetch fails too, so bootstrap recovery runs and
        # must not smuggle the unattributed guest values back in.
        mock_provider.get_custom_fields = AsyncMock(
            side_effect=PMSProviderError("rate limited"),
        )
        await service.sync_listing(listing, mock_provider)

        booking = await repo.get_by_pms_id(listing.id, "RES_UPGRADE_GUEST")
        assert booking is not None
        assert booking.guest_phone_last4 is None
        assert "guest_phone" not in booking.custom_data
        assert "guest_email" not in booking.custom_data
        assert booking.custom_data["door_code"] == "1234"
        assert booking.pms_updated_at is None


def _sync_log_text(caplog) -> str:
    """Join log records emitted by the sync service only.

    Use this for positive assertions about sync service messages.  Privacy
    assertions must use :func:`_all_log_text` instead: filtering by logger
    name hides anything a library logger leaks.
    """
    return "\n".join(
        record.getMessage()
        for record in caplog.records
        if record.name == "src.services.sync_service"
    )


def _all_log_text(caplog) -> str:
    """Join every captured log record, whatever logger emitted it.

    Privacy assertions must cover the complete output, including records
    from SQLAlchemy, aiosqlite and HTTP clients.
    """
    return "\n".join(record.getMessage() for record in caplog.records)


class TestSyncObservabilityLogging:
    """Tests for diagnostic logging emitted by sync_listing."""

    @pytest.mark.asyncio
    async def test_logs_provider_and_retained_counts(
        self, sync_session, test_listing, mock_provider, caplog
    ):
        """Test INFO log reports fetched and post-filter reservation counts."""
        sync_session.add(test_listing)
        await sync_session.commit()
        await sync_session.refresh(test_listing)

        stale_checkout = _test_date(-(OLD_BOOKING_RETENTION_DAYS + 10))
        mock_provider.get_reservations = AsyncMock(
            return_value=[
                _make_reservation(pms_booking_id="RES_KEEP"),
                _make_reservation(
                    pms_booking_id="RES_OLD",
                    check_in=stale_checkout - timedelta(days=3),
                    check_out=stale_checkout,
                ),
            ]
        )

        service = SyncService(sync_session)
        with caplog.at_level(logging.INFO, logger="src.services.sync_service"):
            await service.sync_listing(test_listing, mock_provider)

        assert (
            "Provider returned 2 reservation(s) for listing PROP123; "
            "1 remain after retention filtering" in _sync_log_text(caplog)
        )

    @pytest.mark.asyncio
    async def test_debug_snapshot_has_ids_dates_and_no_pii(
        self, sync_session, test_listing, mock_provider, caplog
    ):
        """Test DEBUG snapshot logs identifiers/dates but never guest data."""
        sync_session.add(test_listing)
        await sync_session.commit()
        await sync_session.refresh(test_listing)

        check_in = _test_date(-1)
        check_out = _test_date(2)
        mock_provider.get_reservations = AsyncMock(
            return_value=[
                _make_reservation(
                    pms_booking_id="RES_PII",
                    guest_name="Jane Doe",
                    check_in=check_in,
                    check_out=check_out,
                    room_ids=("ROOM1",),
                    custom_data={"guest_phone_last4": "4567"},
                ),
            ]
        )

        service = SyncService(sync_session)
        with caplog.at_level(logging.DEBUG, logger="src.services.sync_service"):
            await service.sync_listing(test_listing, mock_provider)

        log_text = _sync_log_text(caplog)
        assert "id=RES_PII" in log_text
        assert "status=confirmed" in log_text
        assert check_in.isoformat() in log_text
        assert check_out.isoformat() in log_text

        # Privacy assertions cover every logger, not just the sync service.
        all_text = _all_log_text(caplog)
        assert "Jane Doe" not in all_text
        assert "4567" not in all_text

    @pytest.mark.asyncio
    async def test_in_progress_guard_logs_booking_id_at_debug(
        self, sync_session, test_listing, mock_provider, caplog
    ):
        """Test the in-progress prune guard logs its decision at DEBUG."""
        sync_session.add(test_listing)
        await sync_session.commit()
        await sync_session.refresh(test_listing)

        booking = Booking(
            listing_id=test_listing.id,
            pms_booking_id="RES_INPROGRESS",
            guest_name="Jane Doe",
            check_in_date=_test_date(-2),
            check_out_date=_test_date(2),
            status="confirmed",
        )
        sync_session.add(booking)
        await sync_session.commit()

        mock_provider.get_reservations = AsyncMock(return_value=[])

        service = SyncService(sync_session)
        with caplog.at_level(logging.DEBUG, logger="src.services.sync_service"):
            result = await service.sync_listing(test_listing, mock_provider)

        assert result["cancelled"] == 0
        log_text = _sync_log_text(caplog)
        assert "In-progress guard kept booking RES_INPROGRESS" in log_text
        assert "Jane Doe" not in _all_log_text(caplog)


class TestCustomFieldLabels:
    """Tests for provider-supplied custom-field labels (issue #127)."""

    OBJECT_ID = "6a5a9f38050dfd00134fe27e"
    MANGLED = "6 A 5 A 9 F 38050 Dfd 00134 Fe 27 E"
    LABEL = "Test door lock code"

    @staticmethod
    async def _listing(session) -> Listing:
        """Create and persist a listing for label tests.

        Args:
            session: Database session.

        Returns:
            Persisted listing.
        """
        listing = Listing(
            pms_id="PROP_LABELS",
            name="Label Property",
            ical_url_slug="label-property",
            enabled=True,
            sync_enabled=True,
        )
        session.add(listing)
        await session.commit()
        await session.refresh(listing)
        return listing

    @staticmethod
    async def _available_field(session, listing_id: int, field_key: str):
        """Fetch a discovered field by key.

        Args:
            session: Database session.
            listing_id: Owning listing ID.
            field_key: Field key to look up.

        Returns:
            The AvailableField row, or None.
        """
        repo = AvailableFieldRepository(session)
        return await repo.get_by_field_key(listing_id, field_key)

    def _reservations(self):
        """Build a reservation whose custom data is keyed by ObjectId.

        Returns:
            Single-element list of PMSReservation.
        """
        return [
            _make_reservation(
                pms_booking_id="RES_LABEL",
                custom_data={self.OBJECT_ID: "8160"},
            )
        ]

    @pytest.mark.asyncio
    async def test_definition_label_names_the_discovered_field(
        self, sync_session, mock_provider
    ):
        """Test the Guesty label replaces the derived ObjectId name."""
        listing = await self._listing(sync_session)
        mock_provider.get_reservations = AsyncMock(return_value=self._reservations())
        mock_provider.get_custom_field_definitions = AsyncMock(
            return_value={self.OBJECT_ID: self.LABEL}
        )

        service = SyncService(sync_session)
        await service.sync_listing(listing, mock_provider)

        field = await self._available_field(sync_session, listing.id, self.OBJECT_ID)
        assert field is not None
        assert field.display_name == self.LABEL

    @pytest.mark.asyncio
    async def test_custom_data_keys_are_not_rewritten(
        self, sync_session, mock_provider
    ):
        """Test configured fields keep working: keys stay the raw fieldId.

        Re-keying by label would silently break every install whose
        configured field names reference the ObjectId.
        """
        listing = await self._listing(sync_session)
        mock_provider.get_reservations = AsyncMock(return_value=self._reservations())
        mock_provider.get_custom_field_definitions = AsyncMock(
            return_value={self.OBJECT_ID: self.LABEL}
        )

        service = SyncService(sync_session)
        await service.sync_listing(listing, mock_provider)

        booking_repo = BookingRepository(sync_session)
        bookings = await booking_repo.get_for_listing(listing.id)
        assert bookings[0].custom_data[self.OBJECT_ID] == "8160"

        field = await self._available_field(sync_session, listing.id, self.OBJECT_ID)
        assert field.field_key == self.OBJECT_ID

    @pytest.mark.asyncio
    async def test_existing_mangled_display_name_is_repaired(
        self, sync_session, mock_provider
    ):
        """Test a previously discovered field is renamed on next sync."""
        listing = await self._listing(sync_session)
        repo = AvailableFieldRepository(sync_session)
        await repo.upsert_field(listing.id, self.OBJECT_ID, "8160")
        await sync_session.commit()
        stale = await self._available_field(sync_session, listing.id, self.OBJECT_ID)
        assert stale.display_name == self.OBJECT_ID

        mock_provider.get_reservations = AsyncMock(return_value=self._reservations())
        mock_provider.get_custom_field_definitions = AsyncMock(
            return_value={self.OBJECT_ID: self.LABEL}
        )

        service = SyncService(sync_session)
        await service.sync_listing(listing, mock_provider)

        field = await self._available_field(sync_session, listing.id, self.OBJECT_ID)
        assert field.display_name == self.LABEL

    @pytest.mark.asyncio
    async def test_unedited_custom_field_label_is_repaired(
        self, sync_session, mock_provider
    ):
        """Test the mangled iCal label from issue #127 is replaced."""
        listing = await self._listing(sync_session)
        sync_session.add(
            CustomField(
                listing_id=listing.id,
                field_name=self.OBJECT_ID,
                display_label=self.MANGLED,
                enabled=True,
            )
        )
        await sync_session.commit()

        mock_provider.get_reservations = AsyncMock(return_value=self._reservations())
        mock_provider.get_custom_field_definitions = AsyncMock(
            return_value={self.OBJECT_ID: self.LABEL}
        )

        service = SyncService(sync_session)
        await service.sync_listing(listing, mock_provider)

        repo = CustomFieldRepository(sync_session)
        field = await repo.get_by_field_name(listing.id, self.OBJECT_ID)
        assert field.display_label == self.LABEL

    @pytest.mark.asyncio
    async def test_user_edited_custom_field_label_is_preserved(
        self, sync_session, mock_provider
    ):
        """Test a label the user chose is never overwritten."""
        listing = await self._listing(sync_session)
        sync_session.add(
            CustomField(
                listing_id=listing.id,
                field_name=self.OBJECT_ID,
                display_label="Door Code",
                enabled=True,
            )
        )
        await sync_session.commit()

        mock_provider.get_reservations = AsyncMock(return_value=self._reservations())
        mock_provider.get_custom_field_definitions = AsyncMock(
            return_value={self.OBJECT_ID: self.LABEL}
        )

        service = SyncService(sync_session)
        await service.sync_listing(listing, mock_provider)

        repo = CustomFieldRepository(sync_session)
        field = await repo.get_by_field_name(listing.id, self.OBJECT_ID)
        assert field.display_label == "Door Code"

    @pytest.mark.asyncio
    async def test_sync_completes_when_definitions_are_forbidden(
        self, sync_session, mock_provider, caplog
    ):
        """Test a raising definitions lookup cannot fail a listing sync."""
        listing = await self._listing(sync_session)
        mock_provider.get_reservations = AsyncMock(return_value=self._reservations())
        mock_provider.get_custom_field_definitions = AsyncMock(
            side_effect=PMSProviderError("HTTP 403: forbidden")
        )

        service = SyncService(sync_session)
        with caplog.at_level(logging.WARNING):
            counts = await service.sync_listing(listing, mock_provider)

        assert counts["inserted"] == 1
        assert "label lookup failed" in caplog.text
        field = await self._available_field(sync_session, listing.id, self.OBJECT_ID)
        assert field.display_name == self.OBJECT_ID

    @pytest.mark.asyncio
    async def test_sync_completes_when_definitions_are_malformed(
        self, sync_session, mock_provider
    ):
        """Test a non-mapping definitions result is ignored, not applied."""
        listing = await self._listing(sync_session)
        mock_provider.get_reservations = AsyncMock(return_value=self._reservations())
        mock_provider.get_custom_field_definitions = AsyncMock(
            return_value=["not", "a", "mapping"]
        )

        service = SyncService(sync_session)
        counts = await service.sync_listing(listing, mock_provider)

        assert counts["inserted"] == 1
        field = await self._available_field(sync_session, listing.id, self.OBJECT_ID)
        assert field.display_name == self.OBJECT_ID

    @pytest.mark.asyncio
    async def test_definitions_are_requested_once_per_sync(
        self, sync_session, mock_provider
    ):
        """Test the lookup is per sync, never per reservation.

        Guesty's rate limits are account-wide (issue #128), so this call
        count must not scale with the size of the snapshot.
        """
        listing = await self._listing(sync_session)
        mock_provider.get_reservations = AsyncMock(
            return_value=[
                _make_reservation(
                    pms_booking_id=f"RES_LABEL{i}",
                    custom_data={self.OBJECT_ID: "8160"},
                )
                for i in range(5)
            ]
        )
        mock_provider.get_custom_field_definitions = AsyncMock(
            return_value={self.OBJECT_ID: self.LABEL}
        )

        service = SyncService(sync_session)
        await service.sync_listing(listing, mock_provider)

        assert mock_provider.get_custom_field_definitions.await_count == 1

    @staticmethod
    async def _add_custom_field(session, listing_id: int, name: str, label: str):
        """Persist a configured custom field.

        Args:
            session: Database session.
            listing_id: Owning listing ID.
            name: Field name (the raw provider key).
            label: Stored, user-owned display label.

        Returns:
            None.
        """
        session.add(
            CustomField(
                listing_id=listing_id,
                field_name=name,
                display_label=label,
                enabled=True,
            )
        )
        await session.commit()

    @pytest.mark.asyncio
    async def test_mangled_label_is_repaired_without_definitions(
        self, sync_session, mock_provider
    ):
        """Test a 403 still rescues an upgraded install from issue #127.

        CalendarService renders the stored label, so leaving it alone
        when definitions are unavailable would export the mangled text
        forever.  A raw ObjectId is ugly but honest.
        """
        listing = await self._listing(sync_session)
        await self._add_custom_field(
            sync_session, listing.id, self.OBJECT_ID, self.MANGLED
        )
        mock_provider.get_reservations = AsyncMock(return_value=self._reservations())
        mock_provider.get_custom_field_definitions = AsyncMock(
            side_effect=PMSProviderError("HTTP 403: forbidden")
        )

        service = SyncService(sync_session)
        await service.sync_listing(listing, mock_provider)

        repo = CustomFieldRepository(sync_session)
        field = await repo.get_by_field_name(listing.id, self.OBJECT_ID)
        assert field.display_label == self.OBJECT_ID

    @pytest.mark.asyncio
    async def test_mangled_label_is_repaired_when_definition_is_missing(
        self, sync_session, mock_provider
    ):
        """Test a field absent from a populated mapping is repaired too."""
        other_id = "70f1c2d3e4a5b60718293a4b"
        listing = await self._listing(sync_session)
        await self._add_custom_field(
            sync_session, listing.id, self.OBJECT_ID, self.MANGLED
        )
        mock_provider.get_reservations = AsyncMock(return_value=self._reservations())
        mock_provider.get_custom_field_definitions = AsyncMock(
            return_value={other_id: "Some other field"}
        )

        service = SyncService(sync_session)
        await service.sync_listing(listing, mock_provider)

        repo = CustomFieldRepository(sync_session)
        field = await repo.get_by_field_name(listing.id, self.OBJECT_ID)
        assert field.display_label == self.OBJECT_ID

    @pytest.mark.asyncio
    async def test_mangled_discovered_name_is_repaired_without_label(
        self, sync_session, mock_provider
    ):
        """Test a previously mangled discovered name is repaired too."""
        listing = await self._listing(sync_session)
        sync_session.add(
            AvailableField(
                listing_id=listing.id,
                field_key=self.OBJECT_ID,
                display_name=self.MANGLED,
                sample_value="8160",
            )
        )
        await sync_session.commit()
        mock_provider.get_reservations = AsyncMock(return_value=self._reservations())
        mock_provider.get_custom_field_definitions = AsyncMock(return_value={})

        service = SyncService(sync_session)
        await service.sync_listing(listing, mock_provider)

        discovered = await self._available_field(
            sync_session, listing.id, self.OBJECT_ID
        )
        assert discovered.display_name == self.OBJECT_ID

    @pytest.mark.asyncio
    async def test_user_label_survives_missing_definitions(
        self, sync_session, mock_provider
    ):
        """Test the raw-ID fallback never touches an edited label."""
        listing = await self._listing(sync_session)
        await self._add_custom_field(
            sync_session, listing.id, self.OBJECT_ID, "Door Code"
        )
        mock_provider.get_reservations = AsyncMock(return_value=self._reservations())
        mock_provider.get_custom_field_definitions = AsyncMock(return_value={})

        service = SyncService(sync_session)
        await service.sync_listing(listing, mock_provider)

        repo = CustomFieldRepository(sync_session)
        field = await repo.get_by_field_name(listing.id, self.OBJECT_ID)
        assert field.display_label == "Door Code"

    @pytest.mark.asyncio
    async def test_repaired_label_upgrades_once_definitions_return(
        self, sync_session, mock_provider
    ):
        """Test the raw-ID repair is not a dead end for later syncs."""
        listing = await self._listing(sync_session)
        await self._add_custom_field(
            sync_session, listing.id, self.OBJECT_ID, self.MANGLED
        )
        mock_provider.get_reservations = AsyncMock(return_value=self._reservations())
        mock_provider.get_custom_field_definitions = AsyncMock(return_value={})

        service = SyncService(sync_session)
        await service.sync_listing(listing, mock_provider)

        mock_provider.get_custom_field_definitions = AsyncMock(
            return_value={self.OBJECT_ID: self.LABEL}
        )
        await service.sync_listing(listing, mock_provider)

        repo = CustomFieldRepository(sync_session)
        field = await repo.get_by_field_name(listing.id, self.OBJECT_ID)
        assert field.display_label == self.LABEL
