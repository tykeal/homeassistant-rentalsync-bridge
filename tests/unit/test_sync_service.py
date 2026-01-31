# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for sync service."""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from src.database import Base
from src.models.booking import Booking
from src.models.listing import Listing
from src.models.oauth_credential import OAuthCredential
from src.services.calendar_service import CalendarCache
from src.services.cloudbeds_service import CloudbedsService
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
def test_listing(sync_session):
    """Create test listing."""
    listing = Listing(
        cloudbeds_id="PROP123",
        name="Test Property",
        ical_url_slug="test-property",
        enabled=True,
        sync_enabled=True,
        timezone="America/New_York",
    )
    return listing


@pytest.fixture
def test_credential():
    """Create test OAuth credential."""
    cred = MagicMock(spec=OAuthCredential)
    cred.access_token = "test_access_token"
    cred.refresh_token = "test_refresh_token"
    return cred


class TestSyncService:
    """Tests for SyncService."""

    @pytest.mark.asyncio
    async def test_sync_disabled_listing(self, sync_session, test_credential):
        """Test sync skips disabled listings."""
        listing = Listing(
            cloudbeds_id="DISABLED",
            name="Disabled Listing",
            ical_url_slug="disabled",
            enabled=True,
            sync_enabled=False,  # Sync disabled
        )
        sync_session.add(listing)
        await sync_session.commit()

        service = SyncService(sync_session)
        result = await service.sync_listing(listing, test_credential)

        assert result == {"inserted": 0, "updated": 0, "cancelled": 0}

    @pytest.mark.asyncio
    async def test_sync_creates_new_bookings(
        self, sync_session, test_listing, test_credential
    ):
        """Test sync creates new bookings from reservations."""
        sync_session.add(test_listing)
        await sync_session.commit()
        await sync_session.refresh(test_listing)

        mock_reservations = [
            {
                "id": "RES001",
                "guestName": "John Smith",
                "guestPhone": "555-123-4567",
                "startDate": "2026-03-01",
                "endDate": "2026-03-05",
                "status": "confirmed",
            }
        ]

        with patch(
            "src.services.sync_service.CloudbedsService"
        ) as mock_cloudbeds_class:
            mock_cloudbeds = AsyncMock()
            mock_cloudbeds.get_reservations = AsyncMock(return_value=mock_reservations)
            mock_cloudbeds_class.return_value = mock_cloudbeds
            # Keep static method working
            mock_cloudbeds_class.extract_phone_last4 = (
                CloudbedsService.extract_phone_last4
            )

            service = SyncService(sync_session)
            result = await service.sync_listing(test_listing, test_credential)

        assert result["inserted"] == 1
        assert result["updated"] == 0
        assert result["cancelled"] == 0

    @pytest.mark.asyncio
    async def test_sync_updates_existing_bookings(
        self, sync_session, test_listing, test_credential
    ):
        """Test sync updates existing bookings."""
        sync_session.add(test_listing)
        await sync_session.commit()
        await sync_session.refresh(test_listing)

        # Create existing booking
        booking = Booking(
            listing_id=test_listing.id,
            cloudbeds_booking_id="RES002",
            guest_name="Old Name",
            check_in_date=datetime(2026, 3, 1, tzinfo=UTC),
            check_out_date=datetime(2026, 3, 5, tzinfo=UTC),
            status="confirmed",
        )
        sync_session.add(booking)
        await sync_session.commit()

        mock_reservations = [
            {
                "id": "RES002",
                "guestName": "New Name",  # Updated name
                "startDate": "2026-03-01",
                "endDate": "2026-03-05",
                "status": "confirmed",
            }
        ]

        with patch(
            "src.services.sync_service.CloudbedsService"
        ) as mock_cloudbeds_class:
            mock_cloudbeds = AsyncMock()
            mock_cloudbeds.get_reservations = AsyncMock(return_value=mock_reservations)
            mock_cloudbeds_class.return_value = mock_cloudbeds
            # Keep static method working
            mock_cloudbeds_class.extract_phone_last4 = (
                CloudbedsService.extract_phone_last4
            )

            service = SyncService(sync_session)
            result = await service.sync_listing(test_listing, test_credential)

        assert result["inserted"] == 0
        assert result["updated"] == 1
        assert result["cancelled"] == 0

    @pytest.mark.asyncio
    async def test_sync_marks_cancelled(
        self, sync_session, test_listing, test_credential
    ):
        """Test sync marks bookings as cancelled when not in fetch."""
        sync_session.add(test_listing)
        await sync_session.commit()
        await sync_session.refresh(test_listing)

        # Create existing booking that won't be in the sync
        booking = Booking(
            listing_id=test_listing.id,
            cloudbeds_booking_id="RES_GONE",
            guest_name="Cancelled Guest",
            check_in_date=datetime(2026, 3, 1, tzinfo=UTC),
            check_out_date=datetime(2026, 3, 5, tzinfo=UTC),
            status="confirmed",
        )
        sync_session.add(booking)
        await sync_session.commit()

        # Empty reservation list (booking was cancelled)
        mock_reservations: list = []

        with patch(
            "src.services.sync_service.CloudbedsService"
        ) as mock_cloudbeds_class:
            mock_cloudbeds = AsyncMock()
            mock_cloudbeds.get_reservations = AsyncMock(return_value=mock_reservations)
            mock_cloudbeds_class.return_value = mock_cloudbeds
            # Keep static method working
            mock_cloudbeds_class.extract_phone_last4 = (
                CloudbedsService.extract_phone_last4
            )

            service = SyncService(sync_session)
            result = await service.sync_listing(test_listing, test_credential)

        assert result["inserted"] == 0
        assert result["updated"] == 0
        assert result["cancelled"] == 1

    @pytest.mark.asyncio
    async def test_sync_invalidates_cache(
        self, sync_session, test_listing, test_credential
    ):
        """Test sync invalidates calendar cache when changes occur."""
        sync_session.add(test_listing)
        await sync_session.commit()
        await sync_session.refresh(test_listing)

        cache = CalendarCache()
        cache.set(test_listing.ical_url_slug, "cached_ical")

        mock_reservations = [
            {
                "id": "RES003",
                "guestName": "New Guest",
                "startDate": "2026-03-01",
                "endDate": "2026-03-05",
                "status": "confirmed",
            }
        ]

        with patch(
            "src.services.sync_service.CloudbedsService"
        ) as mock_cloudbeds_class:
            mock_cloudbeds = AsyncMock()
            mock_cloudbeds.get_reservations = AsyncMock(return_value=mock_reservations)
            mock_cloudbeds_class.return_value = mock_cloudbeds
            # Keep static method working
            mock_cloudbeds_class.extract_phone_last4 = (
                CloudbedsService.extract_phone_last4
            )

            service = SyncService(sync_session, calendar_cache=cache)
            await service.sync_listing(test_listing, test_credential)

        # Cache should be invalidated
        assert cache.get(test_listing.ical_url_slug) is None

    @pytest.mark.asyncio
    async def test_sync_handles_api_error(
        self, sync_session, test_listing, test_credential
    ):
        """Test sync raises error on API failure."""
        sync_session.add(test_listing)
        await sync_session.commit()

        with patch(
            "src.services.sync_service.CloudbedsService"
        ) as mock_cloudbeds_class:
            from src.services.cloudbeds_service import CloudbedsServiceError

            mock_cloudbeds = AsyncMock()
            mock_cloudbeds.get_reservations = AsyncMock(
                side_effect=CloudbedsServiceError("API Error")
            )
            mock_cloudbeds_class.return_value = mock_cloudbeds

            service = SyncService(sync_session)
            with pytest.raises(SyncServiceError):
                await service.sync_listing(test_listing, test_credential)


class TestExtractBookingData:
    """Tests for booking data extraction."""

    @pytest.fixture
    def service(self, sync_session):
        """Create sync service."""
        return SyncService(sync_session)

    def test_extract_guest_name_direct(self, service):
        """Test extracting guest name directly."""
        reservation = {"guestName": "Jane Doe"}
        result = service._extract_booking_data(reservation)

        assert result["guest_name"] == "Jane Doe"

    def test_extract_guest_name_from_parts(self, service):
        """Test extracting guest name from first/last."""
        reservation = {"guestFirstName": "Jane", "guestLastName": "Doe"}
        result = service._extract_booking_data(reservation)

        assert result["guest_name"] == "Jane Doe"

    def test_extract_phone_last4(self, service):
        """Test extracting phone last 4."""
        reservation = {"guestPhone": "+1 (555) 123-4567"}
        result = service._extract_booking_data(reservation)

        assert result["guest_phone_last4"] == "4567"

    def test_extract_status_normalization(self, service):
        """Test status is normalized to lowercase."""
        reservation = {"status": "CONFIRMED"}
        result = service._extract_booking_data(reservation)

        assert result["status"] == "confirmed"

    def test_extract_status_unknown_defaults_confirmed(self, service):
        """Test unknown status defaults to confirmed."""
        reservation = {"status": "UNKNOWN_STATUS"}
        result = service._extract_booking_data(reservation)

        assert result["status"] == "confirmed"


class TestParseDate:
    """Tests for date parsing."""

    def test_parse_iso_date(self):
        """Test parsing ISO date format."""
        result = SyncService._parse_date("2026-03-15")

        assert result == datetime(2026, 3, 15, tzinfo=UTC)

    def test_parse_datetime(self):
        """Test parsing datetime format."""
        result = SyncService._parse_date("2026-03-15T14:30:00")

        assert result == datetime(2026, 3, 15, 14, 30, 0, tzinfo=UTC)

    def test_parse_none(self):
        """Test parsing None returns None."""
        result = SyncService._parse_date(None)

        assert result is None

    def test_parse_invalid(self):
        """Test parsing invalid format returns None."""
        result = SyncService._parse_date("not-a-date")

        assert result is None


class TestListingIsolation:
    """Tests for listing isolation during sync."""

    @pytest.mark.asyncio
    async def test_sync_does_not_affect_other_listings(
        self, sync_session, test_credential
    ):
        """Test that syncing one listing doesn't affect bookings from other listings."""
        # Create two listings
        listing1 = Listing(
            cloudbeds_id="PROP_A",
            name="Property A",
            ical_url_slug="property-a",
            enabled=True,
            sync_enabled=True,
            timezone="America/New_York",
        )
        listing2 = Listing(
            cloudbeds_id="PROP_B",
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

        # Create existing booking for listing2
        booking2 = Booking(
            listing_id=listing2.id,
            cloudbeds_booking_id="RES_B001",
            guest_name="Guest B",
            check_in_date=datetime(2026, 4, 1, tzinfo=UTC),
            check_out_date=datetime(2026, 4, 5, tzinfo=UTC),
            status="confirmed",
        )
        sync_session.add(booking2)
        await sync_session.commit()

        # Sync listing1 with empty reservations
        mock_reservations: list = []

        with patch(
            "src.services.sync_service.CloudbedsService"
        ) as mock_cloudbeds_class:
            mock_cloudbeds = AsyncMock()
            mock_cloudbeds.get_reservations = AsyncMock(return_value=mock_reservations)
            mock_cloudbeds_class.return_value = mock_cloudbeds
            mock_cloudbeds_class.extract_phone_last4 = (
                CloudbedsService.extract_phone_last4
            )

            service = SyncService(sync_session)
            result = await service.sync_listing(listing1, test_credential)

        # Listing1 sync shouldn't affect listing2's booking
        assert result["cancelled"] == 0

        # Verify listing2's booking is still confirmed
        await sync_session.refresh(booking2)
        assert booking2.status == "confirmed"

    @pytest.mark.asyncio
    async def test_sync_only_cancels_own_listing_bookings(
        self, sync_session, test_credential
    ):
        """Test that sync only cancels bookings belonging to that listing."""
        # Create two listings
        listing1 = Listing(
            cloudbeds_id="PROP_X",
            name="Property X",
            ical_url_slug="property-x",
            enabled=True,
            sync_enabled=True,
        )
        listing2 = Listing(
            cloudbeds_id="PROP_Y",
            name="Property Y",
            ical_url_slug="property-y",
            enabled=True,
            sync_enabled=True,
        )
        sync_session.add_all([listing1, listing2])
        await sync_session.commit()
        await sync_session.refresh(listing1)
        await sync_session.refresh(listing2)

        # Create bookings for both listings
        booking1 = Booking(
            listing_id=listing1.id,
            cloudbeds_booking_id="RES_X001",
            guest_name="Guest X",
            check_in_date=datetime(2026, 5, 1, tzinfo=UTC),
            check_out_date=datetime(2026, 5, 5, tzinfo=UTC),
            status="confirmed",
        )
        booking2 = Booking(
            listing_id=listing2.id,
            cloudbeds_booking_id="RES_Y001",
            guest_name="Guest Y",
            check_in_date=datetime(2026, 5, 10, tzinfo=UTC),
            check_out_date=datetime(2026, 5, 15, tzinfo=UTC),
            status="confirmed",
        )
        sync_session.add_all([booking1, booking2])
        await sync_session.commit()

        # Sync listing1 with empty (booking cancelled)
        with patch(
            "src.services.sync_service.CloudbedsService"
        ) as mock_cloudbeds_class:
            mock_cloudbeds = AsyncMock()
            mock_cloudbeds.get_reservations = AsyncMock(return_value=[])
            mock_cloudbeds_class.return_value = mock_cloudbeds
            mock_cloudbeds_class.extract_phone_last4 = (
                CloudbedsService.extract_phone_last4
            )

            service = SyncService(sync_session)
            result = await service.sync_listing(listing1, test_credential)

        # Only listing1's booking should be cancelled
        assert result["cancelled"] == 1

        await sync_session.refresh(booking1)
        await sync_session.refresh(booking2)
        assert booking1.status == "cancelled"
        assert booking2.status == "confirmed"  # Unaffected


class TestSyncStatusTracking:
    """Tests for sync status tracking (last_sync_at, last_sync_error)."""

    @pytest.mark.asyncio
    async def test_sync_updates_last_sync_at_on_success(
        self, sync_session, test_credential
    ):
        """Test that last_sync_at is updated on successful sync."""
        listing = Listing(
            cloudbeds_id="SYNC_STATUS",
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

        with patch(
            "src.services.sync_service.CloudbedsService"
        ) as mock_cloudbeds_class:
            mock_cloudbeds = AsyncMock()
            mock_cloudbeds.get_reservations = AsyncMock(return_value=[])
            mock_cloudbeds_class.return_value = mock_cloudbeds
            mock_cloudbeds_class.extract_phone_last4 = (
                CloudbedsService.extract_phone_last4
            )

            service = SyncService(sync_session)
            result = await service.sync_listing(listing, test_credential)

        assert result is not None
        # last_sync_at should be set
        assert listing.last_sync_at is not None
        assert listing.last_sync_error is None

    @pytest.mark.asyncio
    async def test_sync_clears_last_sync_error_on_success(
        self, sync_session, test_credential
    ):
        """Test that last_sync_error is cleared on successful sync."""
        listing = Listing(
            cloudbeds_id="SYNC_ERROR_CLEAR",
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

        with patch(
            "src.services.sync_service.CloudbedsService"
        ) as mock_cloudbeds_class:
            mock_cloudbeds = AsyncMock()
            mock_cloudbeds.get_reservations = AsyncMock(return_value=[])
            mock_cloudbeds_class.return_value = mock_cloudbeds
            mock_cloudbeds_class.extract_phone_last4 = (
                CloudbedsService.extract_phone_last4
            )

            service = SyncService(sync_session)
            result = await service.sync_listing(listing, test_credential)

        assert result is not None
        # last_sync_error should be cleared
        assert listing.last_sync_error is None
        assert listing.last_sync_at is not None

    @pytest.mark.asyncio
    async def test_sync_updates_last_sync_error_on_failure(
        self, sync_session, test_credential
    ):
        """Test that last_sync_error is set on failed sync."""
        from src.services.cloudbeds_service import CloudbedsServiceError

        listing = Listing(
            cloudbeds_id="SYNC_ERROR",
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

        assert listing.last_sync_error is None

        with patch(
            "src.services.sync_service.CloudbedsService"
        ) as mock_cloudbeds_class:
            mock_cloudbeds = AsyncMock()
            mock_cloudbeds.get_reservations = AsyncMock(
                side_effect=CloudbedsServiceError("API rate limit exceeded")
            )
            mock_cloudbeds_class.return_value = mock_cloudbeds

            service = SyncService(sync_session)
            with pytest.raises(SyncServiceError):
                await service.sync_listing(listing, test_credential)

        # last_sync_error should be set
        assert listing.last_sync_error == "API rate limit exceeded"
        assert listing.last_sync_at is not None
