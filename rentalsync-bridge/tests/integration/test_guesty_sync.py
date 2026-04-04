# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""End-to-end integration test for Guesty PMS provider.

Tests the full flow: credential storage -> property sync -> reservation
sync with guest resolution -> iCal feed generation and content
verification, using mocked Guesty API responses and a real in-memory
SQLite database.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

from icalendar import Calendar
from sqlalchemy import select
from src.models.available_field import AvailableField
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


class TestGuestyEndToEnd:
    """Full Guesty provider integration flow."""

    async def test_full_guesty_sync_flow(self, async_session, async_session_factory):
        """Test full Guesty sync flow."""
        # 1. Store Guesty credentials
        cred = OAuthCredential(
            pms_type="guesty",
            client_id="guesty_client_id",
            client_secret="guesty_secret",
        )
        async_session.add(cred)
        await async_session.commit()
        await async_session.refresh(cred)

        repo = CredentialRepository(async_session)
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
        async_session.add(listing)
        await async_session.commit()
        await async_session.refresh(listing)

        room = make_room(
            listing_id=listing.id,
            pms_room_id="guesty_room_1",
            room_name="Ocean Suite",
            slug="ocean-suite",
        )
        async_session.add(room)
        await async_session.commit()
        await async_session.refresh(room)

        # 3. Sync reservations with guest resolution
        now = datetime.now(UTC)
        mock_provider = AsyncMock()
        mock_provider.provider_type = "guesty"
        mock_provider.has_separate_custom_fields = True
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
                    custom_data={
                        "source": "airbnb",
                        "confirmationCode": "CF001",
                    },
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
        mock_provider.get_custom_fields = AsyncMock(
            return_value={"cf_wifi": "password123"},
        )

        cache = CalendarCache(ttl_seconds=0)
        sync = SyncService(
            async_session,
            calendar_cache=cache,
            session_factory=async_session_factory,
        )
        counts = await sync.sync_listing(listing, mock_provider)

        assert counts["inserted"] == 1
        assert counts["updated"] == 0

        # Verify guest name was resolved
        result = await async_session.execute(select(Booking))
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

    async def test_guesty_multiple_reservations(
        self, async_session, async_session_factory
    ):
        """Test syncing multiple reservations produces distinct iCal events."""
        listing = make_listing(
            pms_id="guesty_multi",
            name="Multi-Booking Villa",
            slug="guesty-multi-villa",
        )
        async_session.add(listing)
        await async_session.commit()
        await async_session.refresh(listing)

        room = make_room(
            listing_id=listing.id,
            pms_room_id="guesty_multi_room",
            room_name="Suite A",
            slug="suite-a",
        )
        async_session.add(room)
        await async_session.commit()
        await async_session.refresh(room)

        now = datetime.now(UTC)
        mock_provider = AsyncMock()
        mock_provider.provider_type = "guesty"
        mock_provider.has_separate_custom_fields = True
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
        mock_provider.get_custom_fields = AsyncMock(return_value={})

        cache = CalendarCache(ttl_seconds=0)
        sync = SyncService(
            async_session,
            calendar_cache=cache,
            session_factory=async_session_factory,
        )
        counts = await sync.sync_listing(listing, mock_provider)
        assert counts["inserted"] == 3

        result = await async_session.execute(
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

    async def test_guesty_field_discovery(self, async_session, async_session_factory):
        """Test that custom fields are discovered after sync."""
        listing = make_listing(
            pms_id="guesty_fields",
            name="Field Discovery Villa",
            slug="guesty-field-discovery",
        )
        async_session.add(listing)
        await async_session.commit()
        await async_session.refresh(listing)

        now = datetime.now(UTC)
        mock_provider = AsyncMock()
        mock_provider.provider_type = "guesty"
        mock_provider.has_separate_custom_fields = True
        mock_provider.get_reservations = AsyncMock(
            return_value=[
                PMSReservation(
                    pms_booking_id="GRF001",
                    listing_pms_id="guesty_fields",
                    guest_name=None,
                    guest_id="gf_42",
                    check_in=now + timedelta(days=5),
                    check_out=now + timedelta(days=8),
                    status="confirmed",
                    room_ids=(),
                    custom_data={
                        "source": "airbnb",
                        "confirmationCode": "CF001",
                    },
                ),
            ]
        )
        mock_provider.get_guest = AsyncMock(
            return_value=PMSGuest(
                guest_id="gf_42",
                full_name="Field Guest",
                phone="+15551112222",
                email="fields@example.com",
            )
        )
        mock_provider.get_custom_fields = AsyncMock(
            return_value={"cf_wifi": "password123"},
        )

        sync = SyncService(
            async_session,
            calendar_cache=CalendarCache(ttl_seconds=0),
            session_factory=async_session_factory,
        )
        await sync.sync_listing(listing, mock_provider)

        fields_result = await async_session.execute(
            select(AvailableField).where(
                AvailableField.listing_id == listing.id,
            )
        )
        field_keys = {f.field_key for f in fields_result.scalars().all()}
        assert "source" in field_keys
        assert "confirmationCode" in field_keys
        assert "guest_email" in field_keys
        assert "guest_phone" in field_keys
        assert "cf_wifi" in field_keys
