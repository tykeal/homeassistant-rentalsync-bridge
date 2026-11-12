# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Switching the Guesty reservations API keeps identities (spec 004 SC-011)."""

from __future__ import annotations

from typing import Any

from icalendar import Calendar
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from src.models.listing import Listing
from src.repositories.booking_repository import BookingRepository
from src.services.calendar_service import CalendarCache, CalendarService
from src.services.sync_service import SyncService

from tests.conftest import GuestyFakeTransport, make_listing, make_room


class _Account:
    """A multi-unit and a single-unit listing on one database."""

    def __init__(
        self,
        session: AsyncSession,
        factory: async_sessionmaker[AsyncSession],
        transport: GuestyFakeTransport,
    ) -> None:
        """Hold the collaborators shared by the helpers."""
        self.session = session
        self.factory = factory
        self.transport = transport
        self.listings: list[Listing] = []

    async def add_listing(self, pms_id: str, rooms: list[str]) -> Listing:
        """Create a tracked listing with one room per unit."""
        listing = make_listing(pms_id=pms_id, slug=f"tg-{pms_id[-6:]}", timezone="UTC")
        self.session.add(listing)
        await self.session.commit()
        await self.session.refresh(listing)
        for room_pms_id in rooms:
            self.session.add(
                make_room(
                    listing_id=listing.id,
                    pms_room_id=room_pms_id,
                    slug=f"tg-{room_pms_id[-6:]}",
                )
            )
        await self.session.commit()
        self.listings.append(listing)
        return listing

    def publish(self, items: list[dict[str, Any]]) -> None:
        """Serve *items* through both the V3 and the legacy resources."""
        p = self.transport.payloads
        self.transport.v3_items = items
        self.transport.legacy_reservations = [p.as_legacy(item) for item in items]
        for item in items:
            guest_id = item["guestId"]
            self.transport.guests[guest_id] = p.guests_crud_body(guest_id)

    async def sync(self, reservations_api: str) -> dict[str, dict[str, Any]]:
        """Run one cycle in *reservations_api* mode; return the feed events."""
        provider = self.transport.provider(reservations_api=reservations_api)
        provider.begin_cycle([listing.pms_id for listing in self.listings])
        service = SyncService(
            self.session,
            calendar_cache=CalendarCache(ttl_seconds=0),
            session_factory=self.factory,
        )
        repo = BookingRepository(self.session)
        events: dict[str, dict[str, Any]] = {}
        try:
            for listing in self.listings:
                await service.sync_listing(listing, provider)
                bookings = list(await repo.get_confirmed_for_listing(listing.id))
                for booking in bookings:
                    await self.session.refresh(booking)
                feed = CalendarService(
                    cache=CalendarCache(ttl_seconds=0)
                ).generate_ical(listing, bookings)
                events.update(_events(feed))
        finally:
            await provider.aclose()
        return events

    async def active_ids(self, listing: Listing) -> set[str]:
        """Return a listing's non-cancelled booking IDs."""
        rows = await BookingRepository(self.session).get_for_listing(listing.id)
        for row in rows:
            await self.session.refresh(row)
        return {row.pms_booking_id for row in rows if row.status != "cancelled"}


def _events(feed: str) -> dict[str, dict[str, Any]]:
    """Index a rendered feed's events by UID."""
    events: dict[str, dict[str, Any]] = {}
    for event in Calendar.from_ical(feed).walk("VEVENT"):
        description = str(event.get("DESCRIPTION", ""))
        events[str(event["UID"])] = {
            "SUMMARY": str(event["SUMMARY"]),
            "Booking ID": [
                line
                for line in description.splitlines()
                if line.startswith("Booking ID:")
            ],
        }
    return events


class TestReservationsApiSwitch:
    """V3 → legacy → V3 on one database, without a migration (T064)."""

    async def test_switch_keeps_single_segment_identity(
        self,
        async_session: AsyncSession,
        async_session_factory: async_sessionmaker[AsyncSession],
        guesty_transport: GuestyFakeTransport,
    ) -> None:
        """Single-segment UIDs survive; ``#n`` rows retire only on legacy."""
        p = guesty_transport.payloads
        account = _Account(async_session, async_session_factory, guesty_transport)
        parent, unit_a, unit_b = p.new_id("lst"), p.new_id("lst"), p.new_id("lst")
        single = p.new_id("lst")
        guesty_transport.add_listing(parent, children=[unit_a, unit_b])
        guesty_transport.add_listing(single)
        multi_listing = await account.add_listing(parent, [unit_a, unit_b])
        single_listing = await account.add_listing(single, [single])

        round_trip = p.round_trip_move(parent, unit_a, unit_b)
        in_unit = p.search_item(unit_a, parent_listing_id=parent, check_in=20)
        plain = p.search_item(single, check_in=3, check_out=6)
        account.publish([round_trip, in_unit, plain])
        rid = round_trip["reservationId"]
        first, extra = f"{rid}::{unit_a}", {f"{rid}::{unit_b}", f"{rid}::{unit_a}#2"}

        def single_segment(events: dict[str, Any]) -> dict[str, Any]:
            """Drop the round trip's events."""
            return {
                uid: event
                for uid, event in events.items()
                if event["Booking ID"] != [f"Booking ID: {rid}"]
            }

        v3_events = await account.sync("v3")
        assert await account.active_ids(multi_listing) == {
            first,
            *extra,
            f"{in_unit['reservationId']}::{unit_a}",
        }
        assert await account.active_ids(single_listing) == {
            f"{plain['reservationId']}::{single}"
        }
        assert len(single_segment(v3_events)) == 2

        legacy_events = await account.sync("legacy")
        active = await account.active_ids(multi_listing)
        assert first in active
        assert active.isdisjoint(extra)
        assert single_segment(legacy_events) == single_segment(v3_events)
        # The round trip collapses to its first interval's event, whose
        # UID ({rid}::{unit}) is the one V3 published.
        (collapsed,) = legacy_events.keys() - single_segment(legacy_events).keys()
        assert collapsed in v3_events

        restored = await account.sync("v3")
        assert extra <= await account.active_ids(multi_listing)
        assert first in await account.active_ids(multi_listing)
        assert single_segment(restored) == single_segment(v3_events)
        assert restored.keys() == v3_events.keys()
