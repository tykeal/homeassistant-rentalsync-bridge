# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Backward compatibility integration tests for Cloudbeds provider.

Verifies that pre-existing Cloudbeds installations continue to work
through the provider-agnostic pipeline, and that iCal feed URLs remain
stable after migration.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

from icalendar import Calendar
from sqlalchemy import select
from src.models.booking import Booking
from src.providers.base import PMSReservation
from src.services.calendar_service import CalendarCache, CalendarService
from src.services.sync_service import SyncService

from tests.conftest import make_booking, make_listing, make_room


class TestCloudbedsBackwardCompat:
    """Verify Cloudbeds continues to work through the new pipeline."""

    async def test_existing_cloudbeds_data_survives_sync(
        self, async_session, async_session_factory
    ):
        """Pre-existing data + re-sync produces correct iCal."""
        # Simulate pre-existing Cloudbeds data (no room association)
        listing = make_listing(
            pms_id="cb_prop_100",
            name="Cloudbeds Lodge",
            slug="cloudbeds-lodge",
        )
        async_session.add(listing)
        await async_session.commit()
        await async_session.refresh(listing)

        now = datetime.now(UTC)
        old_booking = make_booking(
            listing_id=listing.id,
            pms_booking_id="CB_BK001",
            guest_name="Bob Legacy",
            check_in_date=now + timedelta(days=14),
            check_out_date=now + timedelta(days=18),
        )
        async_session.add(old_booking)
        await async_session.commit()
        await async_session.refresh(old_booking)

        # Generate iCal BEFORE re-sync
        cal_service = CalendarService(cache=CalendarCache(ttl_seconds=0))
        ical_before = cal_service.generate_ical(listing, [old_booking])
        cal_before = Calendar.from_ical(ical_before)
        uid_before = str(next(iter(cal_before.walk("VEVENT")))["uid"])

        # Re-sync via provider-agnostic pipeline (Cloudbeds mock)
        # Use empty room_ids so booking ID stays "CB_BK001"
        mock_provider = AsyncMock()
        mock_provider.provider_type = "cloudbeds"
        mock_provider.has_separate_custom_fields = False
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
        mock_provider.get_custom_fields = AsyncMock(return_value={})

        sync = SyncService(
            async_session,
            calendar_cache=CalendarCache(ttl_seconds=0),
            session_factory=async_session_factory,
        )
        counts = await sync.sync_listing(listing, mock_provider)

        # Should update, not insert (same booking ID)
        assert counts["inserted"] == 0
        assert counts["updated"] == 1
        assert counts["cancelled"] == 0

        # iCal after sync should have same UID (stable)
        result = await async_session.execute(
            select(Booking).where(Booking.listing_id == listing.id)
        )
        bookings_after = list(result.scalars().all())
        assert len(bookings_after) == 1

        ical_after = cal_service.generate_ical(listing, bookings_after)
        cal_after = Calendar.from_ical(ical_after)
        uid_after = str(next(iter(cal_after.walk("VEVENT")))["uid"])
        assert uid_after == uid_before

    async def test_ical_url_slug_stable_after_migration(
        self, async_session, async_session_factory
    ):
        """iCal URL slugs remain unchanged after migration."""
        original_slug = "my-cloudbeds-property"
        listing = make_listing(
            pms_id="cb_prop_200",
            name="CB Property",
            slug=original_slug,
        )
        async_session.add(listing)
        await async_session.commit()
        await async_session.refresh(listing)

        assert listing.ical_url_slug == original_slug

        room_slug = "standard-room"
        room = make_room(
            listing_id=listing.id,
            pms_room_id="cb_room_2",
            room_name="Standard Room",
            slug=room_slug,
        )
        async_session.add(room)
        await async_session.commit()
        await async_session.refresh(room)

        # Slug should remain unchanged
        assert listing.ical_url_slug == original_slug
        assert room.ical_url_slug == room_slug

    async def test_cloudbeds_sync_with_new_reservation(
        self, async_session, async_session_factory
    ):
        """New reservations from Cloudbeds sync correctly."""
        listing = make_listing(
            pms_id="cb_prop_300",
            name="CB New Reservation Property",
            slug="cb-new-res",
        )
        async_session.add(listing)
        await async_session.commit()
        await async_session.refresh(listing)

        room = make_room(
            listing_id=listing.id,
            pms_room_id="cb_room_3",
            room_name="Deluxe Room",
            slug="deluxe-room",
        )
        async_session.add(room)
        await async_session.commit()
        await async_session.refresh(room)

        now = datetime.now(UTC)
        mock_provider = AsyncMock()
        mock_provider.provider_type = "cloudbeds"
        mock_provider.has_separate_custom_fields = False
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
        mock_provider.get_custom_fields = AsyncMock(return_value={})

        sync = SyncService(
            async_session,
            calendar_cache=CalendarCache(ttl_seconds=0),
            session_factory=async_session_factory,
        )
        counts = await sync.sync_listing(listing, mock_provider)
        assert counts["inserted"] == 1

        result = await async_session.execute(
            select(Booking).where(Booking.listing_id == listing.id)
        )
        bookings = list(result.scalars().all())
        assert bookings[0].guest_name == "Charlie Cloudbeds"

        cal_service = CalendarService(cache=CalendarCache(ttl_seconds=0))
        ical_str = cal_service.generate_ical(listing, bookings)
        assert "Charlie Cloudbeds" in ical_str
        assert "BEGIN:VEVENT" in ical_str
