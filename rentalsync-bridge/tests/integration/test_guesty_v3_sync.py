# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Integration tests for syncing through the Guesty V3 source (spec 004 US1)."""

from __future__ import annotations

import logging
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from src.models.booking import Booking
from src.models.listing import Listing
from src.repositories.booking_repository import BookingRepository
from src.services.calendar_service import CalendarCache
from src.services.sync_service import SyncService

from tests.conftest import (
    GuestyFakeTransport,
    make_booking,
    make_listing,
    make_room,
)

DOOR_CODE = "door_code"


class _Fixture:
    """One tracked single-unit listing, its room and one stay."""

    def __init__(
        self,
        session: AsyncSession,
        factory: async_sessionmaker[AsyncSession],
        transport: GuestyFakeTransport,
    ) -> None:
        """Hold the collaborators the helpers below share."""
        self.session = session
        self.factory = factory
        self.transport = transport
        self.listing: Listing
        self.room_id: int
        self.item: dict[str, Any]

    async def setup(self, *, custom_fields: Any = None, **item: Any) -> None:
        """Create the listing, its room and the account's single stay."""
        p = self.transport.payloads
        pms_id = p.new_id("lst")
        self.transport.add_listing(pms_id)
        self.listing = make_listing(
            pms_id=pms_id, slug=f"v3-{pms_id[-6:]}", timezone="UTC"
        )
        self.session.add(self.listing)
        await self.session.commit()
        await self.session.refresh(self.listing)
        room = make_room(
            listing_id=self.listing.id, pms_room_id=pms_id, slug=f"r-{pms_id[-6:]}"
        )
        self.session.add(room)
        await self.session.commit()
        await self.session.refresh(room)
        self.room_id = room.id
        item.setdefault("check_in", 2)
        item.setdefault("check_out", 5)
        self.item = p.search_item(pms_id, custom_fields=custom_fields, **item)
        self.transport.v3_items = [self.item]
        guest_id = self.item["guestId"]
        self.transport.guests[guest_id] = p.guests_crud_body(guest_id)

    @property
    def booking_id(self) -> str:
        """Return the stay's (single-interval) booking ID."""
        return f"{self.item['reservationId']}::{self.listing.pms_id}"

    async def store(
        self,
        *,
        pms_booking_id: str | None = None,
        custom_data: dict[str, Any] | None = None,
        enrichment_data: dict[str, Any] | None = None,
    ) -> Booking:
        """Store a previously synced booking for the stay."""
        p = self.transport.payloads
        booking = make_booking(
            listing_id=self.listing.id,
            room_id=self.room_id,
            pms_booking_id=pms_booking_id or self.booking_id,
            guest_name="Synthetic Guest",
            check_in_date=p.check_in_at(2),
            check_out_date=p.check_out_at(5),
            custom_data=custom_data,
        )
        booking.enrichment_data = enrichment_data
        self.session.add(booking)
        await self.session.commit()
        await self.session.refresh(booking)
        return booking

    async def sync(self) -> dict[str, int]:
        """Run one V3 sync of the listing."""
        provider = self.transport.provider(reservations_api="v3")
        provider.begin_cycle([self.listing.pms_id])
        service = SyncService(
            self.session,
            calendar_cache=CalendarCache(ttl_seconds=0),
            session_factory=self.factory,
        )
        return await service.sync_listing(self.listing, provider)

    async def bookings(self) -> dict[str, Booking]:
        """Return the listing's stored bookings by booking ID, refreshed."""
        rows = await BookingRepository(self.session).get_for_listing(self.listing.id)
        for row in rows:
            await self.session.refresh(row)
        return {row.pms_booking_id: row for row in rows}


@pytest.fixture
async def v3(
    async_session: AsyncSession,
    async_session_factory: async_sessionmaker[AsyncSession],
    guesty_transport: GuestyFakeTransport,
) -> _Fixture:
    """A V3 sync harness over the counting fake transport."""
    return _Fixture(async_session, async_session_factory, guesty_transport)


class TestInlineCustomFieldEnrichment:
    """Inline custom fields replace per-reservation calls (T020, R8)."""

    async def test_present_makes_no_call(
        self, v3: _Fixture, guesty_transport: GuestyFakeTransport
    ) -> None:
        """Inline values are used directly."""
        p = guesty_transport.payloads
        await v3.setup(custom_fields=p.custom_fields({DOOR_CODE: "4242"}))

        await v3.sync()

        booking = (await v3.bookings())[v3.booking_id]
        assert booking.custom_data[DOOR_CODE] == "4242"
        assert booking.enrichment_data[DOOR_CODE] == "4242"
        assert guesty_transport.counts["custom_fields"] == 0

    async def test_absent_with_empty_enrichment_makes_no_call(
        self, v3: _Fixture, guesty_transport: GuestyFakeTransport
    ) -> None:
        """A stored successful empty read is confirmed without a call."""
        await v3.setup()
        await v3.store(enrichment_data={})

        await v3.sync()

        booking = (await v3.bookings())[v3.booking_id]
        assert DOOR_CODE not in (booking.custom_data or {})
        assert guesty_transport.counts["custom_fields"] == 0

    async def test_absent_never_stored_makes_no_call(
        self, v3: _Fixture, guesty_transport: GuestyFakeTransport
    ) -> None:
        """A new stay has nothing published, so absence is an empty read."""
        await v3.setup()

        await v3.sync()

        assert v3.booking_id in await v3.bookings()
        assert guesty_transport.counts["custom_fields"] == 0

    async def test_absent_with_stored_values_confirms_once(
        self, v3: _Fixture, guesty_transport: GuestyFakeTransport
    ) -> None:
        """Vanished values are confirmed by one call, whose answer wins."""
        await v3.setup()
        guesty_transport.custom_fields[v3.item["reservationId"]] = [
            {"fieldId": DOOR_CODE, "value": "9999"}
        ]
        await v3.store(
            custom_data={DOOR_CODE: "1234"}, enrichment_data={DOOR_CODE: "1234"}
        )

        await v3.sync()

        booking = (await v3.bookings())[v3.booking_id]
        assert booking.custom_data[DOOR_CODE] == "9999"
        assert guesty_transport.counts["custom_fields"] == 1

    async def test_absent_with_stored_values_keeps_last_good_on_failure(
        self, v3: _Fixture, guesty_transport: GuestyFakeTransport
    ) -> None:
        """A failed confirmation keeps the published door code (FR-032)."""
        await v3.setup()
        guesty_transport.fail("custom_fields", 500)
        await v3.store(
            custom_data={DOOR_CODE: "1234"}, enrichment_data={DOOR_CODE: "1234"}
        )

        await v3.sync()

        booking = (await v3.bookings())[v3.booking_id]
        assert booking.custom_data[DOOR_CODE] == "1234"
        assert guesty_transport.counts["custom_fields"] == 1

    async def test_absent_with_null_enrichment_confirms_once(
        self, v3: _Fixture, guesty_transport: GuestyFakeTransport
    ) -> None:
        """Unknown provenance is confirmed by one call."""
        await v3.setup()
        guesty_transport.custom_fields[v3.item["reservationId"]] = [
            {"fieldId": DOOR_CODE, "value": "9999"}
        ]
        await v3.store(custom_data={DOOR_CODE: "1234"}, enrichment_data=None)

        await v3.sync()

        booking = (await v3.bookings())[v3.booking_id]
        assert booking.custom_data[DOOR_CODE] == "9999"
        assert guesty_transport.counts["custom_fields"] == 1

    async def test_absent_with_null_enrichment_bootstraps_on_failure(
        self, v3: _Fixture, guesty_transport: GuestyFakeTransport
    ) -> None:
        """A failed confirmation over a pre-upgrade row recovers its code."""
        await v3.setup()
        guesty_transport.fail("custom_fields", 500)
        await v3.store(custom_data={DOOR_CODE: "1234"}, enrichment_data=None)

        await v3.sync()

        booking = (await v3.bookings())[v3.booking_id]
        assert booking.custom_data[DOOR_CODE] == "1234"
        assert guesty_transport.counts["custom_fields"] == 1

    @pytest.mark.parametrize(
        "malformed", ["not-a-list", [{"value": "0000"}]], ids=["string", "element"]
    )
    @pytest.mark.parametrize("enrichment", [{DOOR_CODE: "1234"}, None])
    async def test_malformed_keeps_last_good(
        self,
        v3: _Fixture,
        guesty_transport: GuestyFakeTransport,
        caplog: pytest.LogCaptureFixture,
        malformed: Any,
        enrichment: dict[str, Any] | None,
    ) -> None:
        """A malformed block warns, keeps the door code and still syncs."""
        await v3.setup(custom_fields=malformed)
        await v3.store(custom_data={DOOR_CODE: "1234"}, enrichment_data=enrichment)

        with caplog.at_level(logging.WARNING):
            await v3.sync()

        booking = (await v3.bookings())[v3.booking_id]
        assert booking.status == "confirmed"
        assert booking.custom_data[DOOR_CODE] == "1234"
        assert guesty_transport.counts["custom_fields"] == 0
        assert any(
            record.levelno == logging.WARNING
            and v3.item["reservationId"] in record.getMessage()
            for record in caplog.records
        )
        assert v3.listing.last_sync_error is None
